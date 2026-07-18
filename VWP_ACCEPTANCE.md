# VWP Acceptance Checklist

이 문서는 로컬 구현 검증과 실제 RHEL/VWP에서만 확인할 수 있는 검증을 구분합니다.
아래 VWP 항목이 모두 통과해야 운영 완료로 판단합니다.

## 이미 확인된 로컬 증거

| 요구사항 | 증거 | 상태 |
|---|---|---|
| 폐쇄망, 가벼운 배포 | runtime 소스만 포함하고 wheel, 데이터, cache 제외 | 통과 |
| Python GUI | PyQt5 import 및 offscreen GUI smoke test | 통과 |
| `/user` 하위 계정 제한 | 실제 경로 및 symlink 범위 검증 단위 테스트 | 통과 |
| 사용률 경고 | `df` 파서와 80/90/95 색상 테스트 | 통과 |
| DB 크기 제한 | 하루 단위 upsert 및 365일 정리 테스트 | 통과 |
| 상세 증가량 | 전체 top-level 기준선 및 delta 테스트 | 통과 |
| 일간/주간 보고서 | scheduler 통합 테스트 | 통과 |
| cron 독립 실행 | cron 생성, 중복 marker 및 프로세스 잠금 테스트 | 부분 통과 |
| 15분 급증 감시 | raw KB 증가율, 6시간·2시간 예상, 공유 FS 중복 제거 테스트 | 통과 |
| 로컬 알림 큐 | outbox 읽음 상태, cooldown 상승, 정상 복귀 테스트 | 통과 |
| 관리자 검색 | PIN 잠금, 별도 DB 검색, 재개·삭제 보정 및 번들 제외 테스트 | 통과 |

`cron` 실제 등록, RHEL coreutils, VWP Qt 화면, 실제 `/user` 권한과 대용량 성능은
개발 환경만으로 증명할 수 없으므로 아래에서 확인합니다.

## 1. 전송 파일 무결성

압축 파일과 `.sha256` 파일을 같은 디렉터리에 옮긴 뒤 실행합니다.

```csh
sha256sum -c storage_manager_vwp-source.tar.gz.sha256
```

결과가 `OK`여야 합니다.

## 2. 압축 해제와 Python 지정

```csh
tar -xzf storage_manager_vwp-source.tar.gz
cd storage_manager_vwp
setenv STORAGE_MANAGER_PYTHON_HOME /path/to/python-3.10
# 또는: setenv STORAGE_MANAGER_PYTHON_BIN /path/to/python3
chmod +x run.csh setup_cron.csh
```

개인 계정은 quota가 제한될 수 있으므로 소스 디렉터리를 기본 데이터 저장소로
사용하지 않습니다. 다음처럼 여유 있는 경로를 지정하거나 최초 `./run.csh`에서
한 번 직접 입력합니다. 최초 경로 검사는 전체 크기를 재귀 순회하지 않아야 합니다.

```csh
setenv STORAGE_MANAGER_DATA_DIR /large/private/path/storage-manager-data
```

여러 관리자가 동일한 데이터 디렉터리와 SQLite 파일을 공유하지 않습니다.
아래 수동 검증 명령을 그대로 사용하려면 GUI에서 선택한 동일 경로를
`STORAGE_MANAGER_DATA_DIR`에도 지정합니다.

프로젝트 계정 내부를 선택하려면 필요할 때 `newgrp`로 전용 폴더를 한 번 생성한 뒤,
`exit` 후 일반 셸에서 `touch`와 `rm`이 성공하는지 확인합니다. 일반 셸 쓰기가
실패하는 경로는 cron 데이터 저장소로 사용하지 않습니다.

## 3. RHEL/VWP 환경 검사

```csh
./run.csh --diagnose
```

합격 기준:

- 마지막 줄이 `0 FAIL`
- Python 3.10 이상이며 실제 실행 파일과 `json` 위치가 의도한 설치 경로
- Qt platform이 `xcb` 또는 사내 환경에서 사용하는 정상 플랫폼
- `/user`가 readable
- `df`, `du`, `find`, `crontab`, `csh`가 모두 `OK`
- NFS 계열 경고가 있으면 데이터 디렉터리가 관리자 전용인지 확인
- 데이터 사용량이 500MB 경고보다 작고 파일·SQLite 쓰기 probe가 `OK`

## 4. GUI와 계정 검사

```csh
./run.csh
```

GUI에서 확인합니다.

- 창이 정상적으로 열림
- 상단 언어 메뉴의 KOR/ENG 전환 즉시 모든 탭과 보고서 언어가 바뀜
- 90~94% 계정은 `주의/WARN`, 95% 이상은 `경고/ALERT`로 표시됨
- byte와 inode 사용률이 각각 표시되고 둘 중 하나가 95% 이상이면 경고됨
- `Dashboard` 컬럼 제목 첫 클릭은 오름차순, 재클릭은 내림차순이며 방향 화살표가 표시됨
- 사용률 정렬 결과가 문자열 순서가 아닌 숫자 순서 `9% < 80% < 100%`와 일치함
- 정렬한 상태에서 새로고침해도 선택 컬럼·방향이 유지되고 수집 결과가 해당 계정 행에 표시됨
- `Accounts`에서 읽기 가능한 `/user/<account>` 등록 가능
- 계정명을 입력하면 `/user/<account>`가, 경로를 입력하면 계정명이 자동 완성됨
- inode 헤더 도움말이 용량이 남아도 inode 100%에서 새 파일 생성이 막힘을 설명함
- 전역 데이터 경로와 같은 파일시스템 계정 등록 시 위험 확인창 표시
- `/tmp`, `/`, `/user/account/subdir` 등록은 거절됨
- `Dashboard` 새로고침 후 `df .`와 같은 filesystem/use% 표시
- 오류 계정이 있어도 창이 멈추지 않음

## 5. 보고서 검사

등록한 계정이 있는 상태에서 상세 `du`를 생략해 빠르게 일간·주간 보고서를 모두
생성합니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 nightly_scan.py \
  --data-dir "$STORAGE_MANAGER_DATA_DIR" --skip-detail --force-weekly
```

다음 파일이 생성되고 등록 계정이 표시되어야 합니다.

```text
data/reports/latest_daily.txt
data/reports/latest_daily_ko.txt
data/reports/latest_daily_en.txt
data/reports/latest_weekly.txt
data/reports/latest_weekly_ko.txt
data/reports/latest_weekly_en.txt
data/reports/latest_cleanup.txt
data/reports/latest_cleanup_ko.txt
data/reports/latest_cleanup_en.txt
```

별도 데이터 환경변수를 사용했다면 `./data` 대신 해당 경로를 사용합니다.

## 6. 상세 스캔 검사

업무 부하가 허용되는 시간에 한 번 실행합니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 nightly_scan.py --data-dir "$STORAGE_MANAGER_DATA_DIR"
```

합격 기준:

- timeout 또는 권한 오류 없이 완료하거나, 실패 계정이 보고서에 명확히 기록됨
- 두 번째 정상 상세 실행부터 `Trend` 탭에 증가 경로가 표시됨
- 진행 중 기준선이 GUI에 작업 수로 표시되고 다음 야간 실행에서 이어짐
- 22시 이후 06시가 지나도 작업이 강제 종료되지 않고 완료 또는 수동 안전 중지됨
- 개별 15분 timeout 항목은 하위 디렉터리 작업으로 나뉘고 전체 deadline은 없음
- 동시 두 번째 실행은 `Another nightly scan is already running`으로 종료됨

## 7. cron 검사

```csh
./setup_cron.csh
crontab -l | grep storage-manager-vwp
```

15분 watcher, 22시 수집, 07시 건강 점검 행이 각각 하나, 총 세 줄이어야 합니다.
`which nice`와 `which ionice`가 모두 성공하면 22시 행의 Python 앞에
`nice -n 10 ionice -c 2 -n 7`이 포함되어야 합니다. 도구가 없는 환경에서는 이
prefix 없이도 cron 설치와 수집이 정상 동작해야 합니다.
watcher를 수동으로 두 번 실행하고 다음 파일을 확인합니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 capacity_watch.py --data-dir "$STORAGE_MANAGER_DATA_DIR"
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 capacity_watch.py --data-dir "$STORAGE_MANAGER_DATA_DIR"
```

```text
data/nightly_scan.log
data/capacity_watch.log
data/capacity_watch_status.json
data/reports/latest_daily.txt
data/reports/latest_cleanup.txt
```

로그와 보고서 날짜가 갱신되면 GUI 종료 상태의 독립 실행까지 검증된 것입니다.

`추적` 탭에서도 다음을 확인합니다.

- cron 상태는 `등록됨 (15분 df + 22:00 상세 + 07:00 건강 점검)`
- 15분 watcher 최근 결과에 filesystem·표본·오류 건수가 표시됨
- `지금 백그라운드 실행` 후 PID와 현재 계정이 표시되고 버튼이 `안전 중지`로 바뀜
- `안전 중지` 후 상태가 `사용자 중지`가 되고 기준선 체크포인트가 유지됨
- `자동 수집 켜기/끄기`가 앱의 세 cron 행만 변경하고 다른 cron 행은 유지함

## 8. MATE 로컬 알림 검사

기본 `파일 outbox` 모드에서 `추적` 탭의 `로그인 시 팝업 알림 자동 시작`을 체크하고
`팝업 알림 시작`, `테스트 알림`을 실행합니다.

합격 기준:

- MATE 트레이 팝업이 표시됨
- `data/notifications/`에 같은 알림의 UTF-8 감사 JSON이 생성됨
- 메인 GUI를 최소화해도 notifier가 독립적으로 새 outbox 팝업을 표시함
- DCV 재접속 후 미확인 수가 유지되고 알림 센터 확인 후 0이 됨
- 로그아웃 중 만든 outbox가 다음 로그인 때 요약 표시됨
- `추적` 탭에서 상태형 notifier 시작·중지와 autostart 체크·해제가 동작함
- 제목 표시줄에 X 닫기 버튼이 없고 최소화/최대화 버튼은 표시됨
- `File > Minimize`와 `Alt+F4`가 실제 종료 대신 창을 최소화함
- `File > Full Exit` 확인 창에서 취소하면 cron/notifier/scan 상태가 바뀌지 않음
- `File > Full Exit` 확인 시 Storage Manager의 15분/22시/07시 cron만 제거되고 다른 cron은 보존됨
- `File > Full Exit` 확인 시 notifier 자동 시작이 제거되고 실행 중 notifier/scan이 안전 종료됨
- `File > Full Exit` 후 기존 수집 데이터와 계정 설정이 그대로 남아 있음
- GUI를 최소화한 상태에서 `health_check.py --data-dir "$STORAGE_MANAGER_DATA_DIR"`를 실행해도 경고가 쌓임

## 9. 관리자 검색 검사

파일 수가 적은 검증용 프로젝트 계정에서 먼저 확인합니다. 상단 `관리자` 메뉴에서
PIN `6368`로 잠금을 해제하고 검색 탭에서 해당 계정의 인덱싱을 켠 뒤 야간 작업을
실행합니다.

합격 기준:

- 잘못된 PIN에서는 검색 탭이 나타나지 않고 올바른 PIN에서만 현재 세션에 표시됨
- 이름 정확 일치·prefix·포함, 확장자, 파일/디렉터리 종류 검색 결과가 실제 경로와 일치
- 검색 탭의 전체/선택 계정 항목 수와 `search_index.db` 실제 크기가 표시됨
- 중간 안전 중지 후 다시 실행하면 완료한 디렉터리부터 재개됨
- 한 폴더의 항목도 500개 단위로 저장되어 대형 flat 디렉터리에서 메모리가 계속 늘지 않음
- 파일 삭제나 rename은 최대 7일 뒤 전체 대조 후 검색 결과에서 사라짐
- 계정 인덱싱을 끄면 검색 행·체크포인트가 정리되고 일반 df·보고서·알림은 계속 동작함
- 배포 압축에 `search_index.db`, sidecar, 실제 인덱싱 경로가 포함되지 않음

## 최종 판정

1~9번이 통과하면 실제 VWP 운영 요구사항이 검증된 것입니다. 실패 출력은 수정 없이
그대로 보관하면 원인 진단에 사용할 수 있습니다.
