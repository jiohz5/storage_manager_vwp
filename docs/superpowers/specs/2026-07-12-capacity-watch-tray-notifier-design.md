# 15분 용량 감시와 로컬 트레이 알림 설계

## 목적

대용량 검증 결과가 수백 GB에서 1TB 이상 빠르게 증가할 때, 메인 GUI와 야간 상세
스캔의 실행 여부에 관계없이 파일시스템의 급증과 full 임박을 조기에 감지한다. 외부
웹, webhook, 메일 서버 없이 RHEL 8.1 MATE 세션 안에서 로컬 팝업으로 사용자에게
알린다.

## 범위

- cron에서 15분마다 byte, inode, 선택적 quota 사용량을 수집한다.
- 90%, 95%, 98%, 100% 상태와 단기 급증 및 full 예상시간을 판정한다.
- 15분 원본 표본은 30일, 일별 집계는 기존 정책대로 365일 보관한다.
- cron은 알림을 로컬 outbox에 영속적으로 기록한다.
- MATE 로그인 세션의 트레이 알림기가 outbox를 읽어 팝업을 표시한다.
- 메인 관리 창을 닫아도 트레이 알림기는 계속 실행한다.
- 세션이 없을 때 발생한 알림은 다음 로그인 때 미확인 요약으로 표시한다.
- 22시 야간 `du/find` 상세 스캔은 현재 구조를 유지한다.

자동 삭제, 자동 작업 종료, 외부 네트워크 전송은 이번 범위에 포함하지 않는다.

## 대안 검토

### 1. 1시간 cron

구성이 단순하지만 한 시간 안에 남은 공간보다 더 큰 결과가 생성되면 full 이전에
경고할 수 없다.

### 2. 15분 cron과 트레이 알림기

`df`는 파일 트리를 순회하지 않으므로 부하가 작고, 메인 GUI가 닫힌 상태에서도
감지와 로컬 알림을 분리할 수 있다. 이 방식을 채택한다.

### 3. cron에서 직접 팝업 실행

cron에는 MATE의 `DISPLAY`, 세션 bus, 인증 환경이 안정적으로 전달되지 않는다.
로그인 세션마다 동작이 달라질 수 있으므로 채택하지 않는다.

## 구성 요소

### Capacity Watch CLI

최상위 `capacity_watch.py`가 cron 진입점이 되고, 핵심 로직은
`storage_manager/capacity_watch.py`에 둔다.

한 번의 실행은 다음 순서를 따른다.

1. 같은 data directory에 대한 중복 watcher 실행을 lock으로 차단한다.
2. 활성 계정 경로를 읽고 `os.stat().st_dev`를 기준으로 동일 파일시스템을 묶는다.
3. 파일시스템마다 대표 경로 한 개에만 `df -Pk`와 `df -Pi`를 실행한다.
4. 설정된 경우에만 계정별 quota command를 실행한다.
5. 원시 `used_kb`, `avail_kb`와 inode 값을 SQLite에 저장한다.
6. 직전 유효 표본과 비교해 증가량, 시간당 증가 속도, full 예상시간을 계산한다.
7. 발생한 이벤트를 기존 알림 outbox에 원자적으로 기록한다.
8. 30일이 지난 고해상도 표본을 삭제하고 실행 상태를 갱신한다.

한 파일시스템 조회가 timeout 또는 권한 오류로 실패해도 다른 파일시스템은 계속
수집한다. 이전 실행이 15분 안에 끝나지 않았다면 다음 실행은 중복 수행하지 않고
건너뛰며, 건강 점검에서 표본 지연을 경고한다.

### Capacity Sample 저장소

기존 `snapshots` 테이블은 하루와 source당 한 행을 유지하므로 15분 자료 저장에는
사용하지 않는다. 별도의 `capacity_samples` 테이블에 다음 값을 저장한다.

- 수집 시각과 계정 ID
- 파일시스템 식별자와 표시 이름
- 전체, 사용, 가용 KB와 사용률
- 전체, 사용, 가용 inode와 사용률
- quota 사용량, 한도, 사용률과 오류

동일 파일시스템의 여러 계정은 같은 측정값을 참조하되 알림은 파일시스템 단위로 한
번만 생성한다. quota가 설정된 계정은 quota 이벤트를 계정 단위로 별도 생성한다.
공유 파일시스템에서 quota 정보가 없으면 특정 계정이 증가 원인이라고 단정하지 않고,
해당 파일시스템에 연결된 계정 목록만 표시한다.

### 단기 분석기

퍼센트는 30TB에서 50TB 파일시스템의 수백 GB 변화를 숨길 수 있으므로 모든 계산은
원시 KB 값을 사용한다.

상태 판정은 다음과 같다.

- `WARN`: byte, inode, quota 중 하나가 90% 이상
- `ALERT`: 설정 임계치 95% 이상 또는 full 예상시간이 6시간 이하
- `EMERGENCY`: 98% 이상 또는 full 예상시간이 2시간 이하
- `FULL`: 사용률 100% 또는 가용 공간 0KB
- `RAPID_GROWTH`: 15분 동안 100GB 이상 증가

100GB 급증 기준과 6시간, 2시간 예측 기준은 설정에서 변경할 수 있게 한다. 예측은
직전 유효 표본과 현재 표본 사이의 양수 증가 속도를 사용한다. 삭제나 측정 오류로
증가량이 0 이하이면 단기 full 예측을 만들지 않는다. 표본이 한 개뿐이면 임계치
판정만 수행한다.

동일 이벤트는 기본 cooldown 동안 반복하지 않는다. `WARN`에서 `ALERT`,
`EMERGENCY`, `FULL`로 심각도가 올라가면 cooldown과 관계없이 다시 알린다. 위험
조건에서 벗어나면 한 번의 `RECOVERY` 알림을 생성한다.

### Cron

기존 cron 설치 기능에 다음 관리 항목을 추가한다.

```cron
7,22,37,52 * * * * <python> <app>/capacity_watch.py --data-dir <data-dir>
0 22 * * * <python> <app>/nightly_scan.py --data-dir <data-dir> --trigger cron
0 7 * * * <python> <app>/health_check.py --data-dir <data-dir>
```

실제 crontab에는 절대 Python 경로, 절대 스크립트 경로, 절대 data directory를
기록한다. 15분 watcher는 `du`, `find`, 기준선 생성 또는 보고서용 상세 스캔을 절대
호출하지 않는다.

### 로컬 알림 큐

기존 `notification_mode=outbox`를 기본값으로 유지한다. cron이 생성한 UTF-8 JSON은
`<data-dir>/notifications/`에 원자적으로 기록한다. 네트워크 연결은 사용하지 않는다.

팝업 읽음 상태는 별도 작은 상태 파일에 저장한다. 알림 파일 생성과 팝업 표시를
분리하므로 그래픽 세션이 없거나 알림기가 잠시 종료되어도 이벤트가 사라지지 않는다.
7일 이내 미확인 이벤트가 여러 개면 로그인 시 개별 팝업을 연속으로 띄우지 않고,
최고 심각도와 건수를 한 번에 요약한다. 상세 내용은 알림 센터에서 확인한다.

### MATE 트레이 알림기

`storage_notifier.py`는 별도 PyQt5 프로세스로 실행한다. 메인 관리 창은 종료할 수
있으며 알림기는 트레이에서 독립적으로 유지된다.

- 새 outbox 이벤트를 주기적으로 확인한다.
- `QSystemTrayIcon`으로 MATE 알림 팝업을 표시한다.
- 미확인 이벤트가 있으면 트레이 상태와 tooltip에 건수를 표시한다.
- 트레이 메뉴에서 관리 창 열기, 알림 센터, 알림기 일시 정지, 완전 종료를 제공한다.
- MATE panel의 tray가 준비되지 않았으면 재시도하고 이벤트는 미확인 상태로 유지한다.
- DCV 연결이 끊겨도 MATE 세션이 유지되는 동안 프로세스는 계속 실행한다.
- 완전히 로그아웃된 동안에는 cron만 동작하며, 다음 로그인 때 누적 경고를 표시한다.

MATE 로그인 자동 시작은 사용자 권한으로
`~/.config/autostart/storage-manager-notifier.desktop`을 생성한다. GUI Tracking 탭과
별도 csh 설치 스크립트에서 설치, 제거, 상태 확인을 제공한다. root 권한은 요구하지
않는다.

### GUI 변경

Tracking 탭에 다음 정보를 추가한다.

- 15분 capacity cron 설치 상태와 다음 실행 예상시간
- 마지막 성공 표본과 소요시간
- 실패한 파일시스템과 오류
- 트레이 알림기 실행 및 MATE autostart 상태
- 알림기 시작, 중지, 재시작과 autostart 설치, 제거 버튼
- 미확인 경고 수와 최근 팝업 결과

설정 탭에는 급증 GB, full 예상 경고시간, full 예상 긴급시간, 고해상도 보존일을
추가한다. 기존 KOR/ENG 전환을 새 화면과 팝업에도 적용한다.

## 데이터 흐름

```text
15분 cron
  -> 계정 목록 로드
  -> 파일시스템별 df 및 선택적 quota
  -> capacity_samples 저장
  -> 증가 속도와 full 예상 계산
  -> 알림 outbox JSON 저장

MATE autostart
  -> storage_notifier.py
  -> 새 outbox 확인
  -> 로컬 팝업 및 미확인 알림 센터

22시 cron
  -> 기존 상세 스캔
  -> 증가 원인 경로와 일간 보고서
```

## 오류와 안전성

- watcher, nightly scan, notifier는 별도 lock과 상태 파일을 사용한다.
- SQLite busy 상황에는 짧은 제한 횟수 재시도를 적용하며 무한 대기하지 않는다.
- outbox와 상태 JSON은 임시 파일 작성 후 `os.replace`로 교체한다.
- notification data directory가 모니터링 대상과 같은 full 파일시스템이면 경고 기록도
  실패할 수 있다. 환경 검증에서 이를 경고하고 가능한 경우 별도 writable
  파일시스템을 권장한다.
- cron 경로에는 interactive shell 설정을 기대하지 않고 절대 Python 경로를 사용한다.
- 자동 삭제나 프로세스 강제 종료는 수행하지 않는다.

## 검증

### 단위 테스트

- 15분 증가량과 시간당 속도 계산
- 90%, 95%, 98%, 100% 상태 전이
- 6시간 및 2시간 full 예상
- 100GB 급증과 삭제 후 음수 증가량 처리
- 공유 파일시스템 중복 `df` 및 중복 알림 방지
- cooldown, 심각도 상승 재알림, recovery 알림
- 30일 고해상도 표본 정리
- watcher lock과 부분 timeout
- 미확인 outbox 요약 및 읽음 상태
- cron 설치, 조회, 제거

### 단위 검증 환경

가짜 storage backend와 임시 data directory로 watcher와 알림 큐를 검증한다. 운영
실행 경로에는 합성 데이터 환경을 포함하지 않는다.

### RHEL 8.1 VWP 검증

- Python, PyQt5, SQLite, `df`, `crontab`, csh 환경 확인
- 15분 cron을 수동 2회 실행해 증가 속도 계산 확인
- MATE 로그인 autostart와 tray 표시 확인
- 메인 GUI 종료 후 팝업 확인
- DCV 연결 해제와 재접속 후 미확인 알림 확인
- MATE 로그아웃 중 발생한 알림의 다음 로그인 요약 확인
- 같은 파일시스템의 여러 계정이 하나의 용량 알림으로 묶이는지 확인
- 야간 상세 스캔과 watcher가 겹쳐도 중단되지 않는지 확인

## Git 및 배포

소스와 설정 예시는 Git에 저장하지만 실제 `accounts.json`, SQLite DB, outbox, 보고서,
로그와 사내 경로는 버전 관리에서 제외한다. 원격 push 인증정보, 비밀번호, token,
SSH private key는 저장소나 대화에 기록하지 않는다.
