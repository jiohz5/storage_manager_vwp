# Storage Manager VWP

폐쇄망 RHEL/VWP에서 여러 프로젝트 계정 경로를 읽기 전용으로
모니터링하는 PyQt5 애플리케이션입니다. 사용자가 전역 데이터 저장소로 명시적으로
선택한 전용 디렉터리를 제외하면 모니터링 대상 계정에는 파일을 쓰거나 삭제하지
않습니다.

## 주요 기능

- 여러 프로젝트 계정 추가, 수정, 비활성화 및 삭제
- `df -Pk/-Pi <account-path>` 기반의 byte·inode 사용률 대시보드
- 선택적 quota JSON command 연동과 공유 filesystem 계정 그룹화
- 사용률 상태: 90% 미만 정상, 90~94% 주의(WARN), 95% 이상 경고(ALERT),
  98% 이상 긴급(EMERGENCY), 100% FULL
- 사용률 색상: 80% 미만 녹색, 80~89% 노란색, 90~94% 주황색,
  95% 이상 빨간색 및 통합 팝업 경고
- 자동 수집 활성 시 GUI 최소화 중에도 15분마다 `df`만 실행하는 경량 capacity watcher
- raw KB 증가량으로 100GB 급증, 6시간/2시간 내 FULL 예상 경고
- 자동 수집 활성 시 GUI 최소화 중에도 매일 22시에 실행되는 cron 야간 작업
- `추적` 탭에서 세 cron, watcher, 현재 PID, notifier 및 최근 결과 확인
- GUI와 독립된 지금 실행과 체크포인트 보존 안전 중지
- 상단 `언어` 메뉴에서 한국어(KOR)/영어(ENG) 즉시 전환
- 일간 보고서와 금요일 주간 보고서
- 7일·30일 추세의 경고/100% 예상일과 평소 대비 급증 이상 탐지
- 07시 건강 점검 cron을 통한 수집 누락·실패 알림
- 검토 전용 정리 후보 보고서 (자동 삭제 없음)
- cron outbox와 MATE 트레이를 이용한 네트워크 없는 로컬 팝업
- 메인 창과 독립 실행되고 로그인 때 자동 시작되는 notifier
- 최대 1년간의 일별 사용률 추이
- 여러 밤에 걸쳐 재개되는 기준선 `du`와 일일 변경 파일 활동 확인
- 관리자 PIN으로만 노출되는 계정별 파일·디렉터리 이름 검색
- 별도 `search_index.db`의 실제 크기와 인덱스 항목 수 표시
- SQLite, JSON, 텍스트 보고서만 사용하는 로컬 저장

## 폐쇄망 반입

애플리케이션 소스에는 Python 런타임이나 wheel을 포함하지 않습니다. 따라서
압축 파일이 작고, 사내에 이미 설치된 Python 3.10과 PyQt5를 그대로 사용합니다.

RHEL에서 압축을 해제한 뒤 csh에서 Python 실행 파일 또는 설치 prefix를 지정합니다.
Python 설치 경로는 읽기 전용이어도 됩니다. `PYTHONHOME`은 표준 라이브러리 검색을
바꾸는 Python 예약 변수이므로 Storage Manager 선택기로 사용하지 않으며, 실행
스크립트는 상속된 값을 경고 후 제거합니다.

```csh
setenv STORAGE_MANAGER_PYTHON_HOME /path/to/python-3.10
# 또는: setenv STORAGE_MANAGER_PYTHON_BIN /path/to/python3
chmod +x run.csh setup_cron.csh
./run.csh
```

개인 계정은 quota가 제한될 수 있으므로 소스 디렉터리를 기본 데이터 위치로 사용하지
않고, 충분한 여유가 있는 별도 위치를 선택하는 것을 권장합니다.
`STORAGE_MANAGER_DATA_DIR`이 없고 저장된 전역 위치도 없으면 최초 GUI 실행에서
SQLite, 보고서, 로그를 둘 디렉터리를 선택합니다. 선택 결과를 기억하는 1KB 미만의
포인터만 `~/.config/storage-manager-vwp/location.json`에 저장됩니다.

```csh
setenv STORAGE_MANAGER_DATA_DIR /large/path/storage-manager-data
./run.csh
```

프로젝트 계정 안에 전용 데이터 폴더를 만들 권한이 `newgrp` 뒤에만 생긴다면 생성만
수동으로 수행합니다. 프로그램은 `newgrp`, `sg`, `sudo`를 자동 실행하지 않습니다.

```csh
newgrp project_group
mkdir -p /user/project_account/.storage-manager-vwp
chmod 700 /user/project_account/.storage-manager-vwp
exit

# 반드시 일반 셸에서 cron과 같은 조건으로 쓰기 확인
touch /user/project_account/.storage-manager-vwp/write_test
rm /user/project_account/.storage-manager-vwp/write_test
```

마지막 검사가 실패하면 해당 경로에는 cron이 DB와 알림을 안정적으로 기록할 수
없으므로 다른 전역 위치를 선택합니다. Python과 데이터 환경변수를 로그인 때마다
유지하려면 개인 `.cshrc`에 `setenv` 행을 넣습니다.

## 환경 확인

가장 간단한 진단 명령은 다음과 같습니다. 선택된 Python 버전·실행 파일, `json`
모듈 위치, SQLite, PyQt5, 사용자 그룹, 데이터 경로·사용량·쓰기 결과를 먼저 출력한
뒤 전체 RHEL 명령 probe를 실행합니다.

```csh
./run.csh --diagnose
```

저장 경로를 명령으로 지정하고 검증할 수도 있습니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 runtime_check.py \
  --set-data-dir /large/path/storage-manager-data
```

점검기는 앱과 동일한 명령을 작은 임시 디렉터리에서 실제 실행합니다. 다음 핵심
항목이 모두 `OK`여야 하며 `WARN`은 내용을 확인한 뒤 진행합니다.

- Python 3.10 이상 (`3.10.9` 지원)
- Python 표준 라이브러리 `json` 위치와 선택된 실제 실행 파일
- PyQt5 import 및 Qt 화면 플랫폼 플러그인 초기화
- SQLite 3.24 이상 및 실제 DB 생성
- 앱이 사용하는 옵션 그대로 실행한 `df -Pk`, `df -Pi`, `du` probe
- RHEL 명령 `find`, `crontab`, `csh`
- `/user` 읽기 권한과 데이터 디렉터리 쓰기 권한
- 데이터 파일시스템 종류와 남은 용량
- MATE 사용자 autostart 디렉터리 쓰기 또는 생성 권한

데이터 위치가 NFS, CIFS, Lustre 등으로 탐지되면 경고가 표시됩니다. 이 경우에도
관리자 한 명의 전용 데이터 디렉터리로는 사용할 수 있지만, 여러 관리자가 같은
SQLite 파일을 동시에 공유하면 안 됩니다.

추가 패키지나 인터넷 연결은 필요하지 않습니다. 그래프도 matplotlib 대신
PyQt5 자체 그리기를 사용합니다.

## 실행과 계정 등록

```csh
./run.csh
```

`Accounts` 탭에서 계정 이름과 `/user/account-name`을 등록합니다. 계정명을 입력하면
첫 번째 모니터링 루트를 prefix로 경로가 자동 완성되고, 경로를 입력하면 마지막
디렉터리명이 계정명으로 자동 완성됩니다. 안전을 위해 존재하고 읽을 수 있는
`/user` 바로 아래 디렉터리만 등록됩니다.

계정 등록에는 자동 `newgrp`가 필요하지 않습니다. 현재 프로세스에서 해당 경로로
`cd`하고 `df`·`du`·`find`에 필요한 읽기 권한이 있으면 됩니다. 전역 데이터 경로와
등록 계정이 같은 파일시스템이면 FULL 상황에서 SQLite와 알림도 기록하지 못할 수
있다는 확인 경고가 표시됩니다.

경로 입력란에는 절대경로를 직접 입력할 수 있습니다. 허용할 상위 경로는
`accounts.json`의 `monitored_roots` 목록으로 관리하며 운영 기본값은
`["/user"]`입니다. 목록 밖의 경로나 상위 경로 자체는 등록되지 않습니다.

직접 실행하려면 다음과 같습니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 app.py \
  --data-dir /large/path/storage-manager-data
```

## 15분·22시·07시 cron 등록

cron은 계속 떠 있는 daemon이 아닙니다. 매시 07·22·37·52분에는
`capacity_watch.py`가 `df`·inode·선택적 quota만 확인하고, 매일 22시에는
`nightly_scan.py`, 매일 07시에는 `health_check.py`를 실행합니다. 15분 watcher는
`du`나 `find`를 호출하지 않습니다.

GUI의 `추적` 탭에서 `자동 수집 켜기`를 누르거나 다음 명령을 한 번 실행합니다.

```csh
./setup_cron.csh
```

등록될 내용을 먼저 확인하려면 다음 명령을 사용합니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 nightly_scan.py \
  --data-dir /large/path/storage-manager-data --print-cron
```

야간 작업을 수동으로 가볍게 확인할 때는 `du`를 생략할 수 있습니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 nightly_scan.py \
  --data-dir /large/path/storage-manager-data --skip-detail
```

`추적` 탭의 상태형 실행 버튼으로 시작한 백그라운드 작업은 GUI를 최소화해도 계속됩니다.
실행 중 같은 버튼은 `안전 중지`로 바뀝니다. 안전 중지는 현재
실행의 run ID에만 요청을 보내고, 실행 중인 `du/find`를 정리한 뒤 완료한 디렉터리
체크포인트를 남깁니다. 임의 PID에 강제 signal을 보내지 않으므로 다른 사용자의
프로세스를 종료하지 않습니다.

15분 watcher를 수동으로 확인하려면 다음 명령을 사용합니다. 증가 속도는 같은
파일시스템의 유효 표본이 두 번 쌓인 뒤 계산됩니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 capacity_watch.py \
  --data-dir /large/path/storage-manager-data
```

## MATE 로컬 팝업

외부 웹이나 방화벽 연결 없이 팝업을 받으려면 기본 알림 모드인 `파일 outbox`를
유지합니다. cron은 경고 JSON을 저장하고 별도 `storage_notifier.py`가 이를 MATE
트레이 팝업으로 표시합니다. 메인 관리 창을 최소화해도 notifier는 독립적으로
동작합니다.

`추적` 탭에서 `로그인 시 팝업 알림 자동 시작`을 체크하고 `팝업 알림 시작`을 누르는
방법을 권장합니다. 명령으로 로그인 자동 시작을 설치할 수도 있습니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 storage_notifier.py \
  --data-dir /large/path/storage-manager-data --install-autostart
```

상태 확인과 자동 시작 해제는 다음과 같습니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 storage_notifier.py \
  --data-dir /large/path/storage-manager-data --status
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 storage_notifier.py \
  --data-dir /large/path/storage-manager-data --remove-autostart
```

DCV 연결만 끊고 MATE 세션이 유지되면 notifier도 계속 실행됩니다. 완전히 로그아웃한
동안에는 cron이 경고를 outbox에 쌓고 다음 로그인 때 7일 이내 미확인 경고를 한 번에
요약합니다. 알림 센터를 확인하기 전에는 읽음 처리하지 않습니다.

실수로 모니터링을 끄지 않도록 메인 창의 제목 표시줄에는 X 닫기 버튼을 두지
않습니다. 제목 표시줄의 최소화 버튼이나 `File > Minimize`를 사용하며,
`Alt+F4` 같은 창 닫기 요청도 실제 종료 대신 최소화합니다.

모든 관리 백그라운드 동작을 끝내려면 `File > Full Exit`를 선택하고 확인합니다.
이 작업은 Storage Manager가 등록한 15분/22시/07시 cron 항목만 제거하고,
notifier 자동 시작을 해제하며, 실행 중인 notifier와 야간 상세 스캔에 안전 종료를
요청합니다. 다른 cron 항목과 수집 데이터는 삭제하지 않습니다. 다시 사용하려면
GUI를 실행해 자동 수집과 notifier 자동 시작을 다시 활성화하면 됩니다.

## Cron 내부 알림

설정 탭의 `cron 알림 모드`는 다음 중 하나입니다. 어떤 모드든 알림 원문과 전송
결과는 데이터 디렉터리에 감사 기록으로 남습니다.

- `파일 outbox`: 기본값. 트레이 notifier가 읽을 `data/notifications/*.json` 생성
- `사내 명령 (stdin)`: JSON argv로 지정한 프로그램을 shell 없이 실행하고 UTF-8
  알림 JSON을 표준입력으로 전달
- `내부 webhook`: 지정한 내부 HTTP(S) 주소에 UTF-8 JSON POST
- `사용 안 함`: 이벤트 생성과 전송을 모두 끔

command 예시는 다음과 같습니다. `{account}` 치환은 quota command에서만 사용하며,
알림 command는 고정 argv를 실행하고 메시지 전체를 stdin으로 받습니다.

```json
{
  "notification_mode": "command",
  "notification_command": ["/opt/company/bin/send-message", "storage-alert"],
  "notification_cooldown_hours": 12
}
```

내부 webhook 예시는 다음과 같습니다.

```json
{
  "notification_mode": "webhook",
  "notification_webhook_url": "https://internal.example/message/storage"
}
```

GUI의 `테스트 알림`으로 outbox 생성을 먼저 확인합니다. 15분 watcher는
byte/inode/quota, 100GB 급증, Full 예상 6시간·2시간, 98%·100%를 판정합니다. 07시
건강 점검은 nightly와 15분 표본 누락·지연 및 최근 비정상 종료를 알립니다. 동일
key·동일 등급은 기본 12시간 억제하고 심각도가 상승하면 즉시 다시 알리며, 정상
복귀 후에는 다음 경고 주기를 새로 시작합니다.

## Quota 어댑터

스토리지별 quota 출력 형식이 다르므로 설정 탭에 shell 없는 command argv를 JSON
배열로 지정합니다. `{account}`와 `{path}`가 치환되며 command는 다음 JSON을 stdout에
출력해야 합니다.

```json
{"used_kb": 950000, "limit_kb": 1000000, "soft_limit_kb": 900000}
```

예: `["/opt/company/bin/quota-json", "{account}", "{path}"]`. 설정하지 않으면 quota
열만 `-`로 표시되고 byte·inode 수집은 계속됩니다.

## 관리자 검색

상단 `관리자` 메뉴에서 현재 고정 PIN `6368`을 입력해 잠금을 해제하면 실행 세션에만
`검색` 탭이 나타납니다. 고정 비밀번호 방식은 소수 운영자를 위한 UI 노출 제한이며
운영체제 권한 통제나 암호화가 아닙니다. 프로그램을 종료하거나 `관리자 잠금`을
누르면 검색 탭이 다시 숨겨집니다.

검색 인덱싱은 계정별 opt-in입니다. 검색 탭에서 선택 계정의 `검색 인덱싱 켜기`를
누르면 다음 22시 작업부터 파일 내용이 아닌 상대 경로, 이름, 확장자, 종류만 별도
`search_index.db`에 저장합니다. 첫 전체 인덱스는 디렉터리 단위 체크포인트로
중단 후 재개하며 심볼릭 링크나 다른 filesystem으로 넘어가지 않습니다. 이후 일일
`find` 결과를 같은 순회에서 반영하고, 삭제·rename은 최대 7일 간격 전체 대조 때
정리합니다.

인덱싱을 끄거나 계정 경로를 바꾸거나 계정을 삭제하면 해당 계정의 검색 행과 진행
체크포인트를 백그라운드에서 정리합니다. GUI가 먼저 종료되어 정리가 끝나지 않아도
다음 22시 작업이 orphan·경로 불일치 행을 다시 정리합니다. SQLite는 삭제한 page를
재사용하므로 일부 계정만 지운 직후 파일 크기가 바로 줄지 않을 수 있습니다. 모든
검색을 끄고 디스크 공간을 즉시 회수해야 한다면 야간 작업과 GUI가 모두 종료된 것을
확인한 뒤 파생 데이터인 `search_index.db*`만 삭제할 수 있으며 다음 활성화 때 다시
구축됩니다.

이름 정확 일치·prefix·포함, 확장자, 파일/디렉터리/링크 종류로 검색할 수 있습니다.
결과는 최대 500개이며 포함 검색은 대규모 DB에서 상대적으로 느릴 수 있지만 GUI
worker에서 실행하므로 메인 창은 응답을 유지합니다. 검색 탭은 전체 DB 실제 크기,
전체 및 선택 계정 항목 수, 마지막 완전 인덱스와 변경 반영 시각을 표시합니다.

## 데이터 크기 제한

생산 스키마에 30일치 15분 표본을 넣은 실측에서 10계정은 약 9.6MB, 20계정은 약
19.2MB였습니다. 20계정에 top-level 항목을 계정당 1,000개 보관하면 약 25MB,
10,000개면 약 77MB였습니다. 보고서·알림·체크포인트·로그를 포함한 일반 예상치는
50~150MB이며 정상 운영 여유로 300MB를 권장합니다. 이 값은 검색 인덱스를 제외한
관리 이력 기준입니다. 기본 500MB에 도달하면 07시 건강 점검과 설정 화면에서
경고하며 자동 삭제는 하지 않습니다.

검색을 켠 계정은 파일·디렉터리마다 경로 행이 추가되므로 별도 여유 공간이
필요합니다. 실험용 스키마의 평균 경로 길이 기준 측정치는 항목당 약 497 byte였고
대략 100만 항목 0.5GB, 1,000만 항목 4.6GB, 5,000만 항목 23GB입니다. 실제 값은
경로 길이에 따라 달라지므로 검색 탭의 `검색 DB 실제 크기`를 우선 확인해야 합니다.

- GUI를 몇 번 새로고침해도 계정별 GUI 스냅샷은 하루 1행만 갱신됩니다.
- 야간 스냅샷도 계정별 하루 1행입니다.
- 15분 원본 표본은 기본 30일 후 삭제되고 일별 이력은 365일 유지됩니다.
- 상세 이력과 보고서는 기본 365일 후 삭제됩니다.
- cron 로그는 5MB가 되면 회전하며 3개까지만 보관합니다.
- 현재 top-level 전체 기준선은 계정별 1세트만 유지합니다.
- 초기/주간 기준선의 체크포인트는 파일이 아니라 디렉터리 작업만 저장합니다.
- 일반 모니터링 DB는 일일 변경 경로 전체를 저장하지 않고 top-level 집계만
  저장합니다. 검색을 켠 계정의 상대 경로는 별도 `search_index.db`에 저장합니다.
- SQLite는 네트워크 경로 호환성을 위해 WAL이 아닌 일반 저널을 사용합니다.
- 최근 야간 실행 상태는 작은 `nightly_scan_status.json` 한 파일에 덮어씁니다.
- 알림 cooldown과 최근 전송 상태도 작은 JSON 한 세트만 유지합니다.
- 알림 outbox, 일간·주간·정리 후보 보고서는 이력 보존기간 후 삭제됩니다.

여러 관리자가 같은 네트워크상의 SQLite 파일을 동시에 공유하는 구성은 권장하지
않습니다. 관리자별 데이터 디렉터리를 사용하세요. 중앙 공유가 필요해지면 별도
서버형 수집 구조로 전환하는 편이 안전합니다.

## 대용량 계정 스캔 정책

`df` 검사는 모든 활성 계정에서 먼저 끝냅니다. 그 후 상세 작업을 실행하므로 한
계정이 느려도 다른 계정의 사용률 보고가 누락되지 않습니다.

- cron은 22:00에 시작하며 06:00 자동 중지 없이 완료될 때까지 실행
- 다음 22:00에도 이전 작업이 남아 있으면 공통 lock이 중복 실행을 차단
- RHEL에서 사용 가능한 경우 `nice -n 10`과 `ionice -c 2 -n 7`로 낮은 우선순위 적용
- `nice`/`ionice`가 없으면 우선순위 prefix 없이 같은 기능을 계속 실행
- 개별 `du` 디렉터리 작업 제한시간은 기본 15분이며 큰 항목은 하위 작업으로 분할
- 전체 작업 deadline은 두지 않으며, 필요할 때 상태형 버튼의 `안전 중지`로 요청
- 날짜별 시작 계정을 순환해 여러 대형 계정의 순서를 공정하게 배분
- 95% 이상 계정을 상세 스캔에서 우선 처리
- 초기 기준선은 완료된 디렉터리 작업을 SQLite에 저장하고 다음 밤에 이어서 수행
- 검색 전체 인덱스는 한 디렉터리도 500개씩 커밋하고 내부에서 안전 중지를 확인함
- 매우 평평한 한 디렉터리는 재개 때 목록을 다시 읽지만 이미 저장한 batch는 다시 쓰지 않음
- 큰 디렉터리가 timeout되면 한 단계 하위 디렉터리 작업으로 나누어 재시도
- 완료 직전 top-level 목록을 다시 대조해 스캔 중 생성·삭제된 항목을 작업표에 반영
- 기준선과 변경 cursor를 DB에 기록한 뒤에만 체크포인트를 제거해 중단 후 재실행 안전
- 초기 기준선 이후에는 `find -newermt`로 `mtime + 현재 byte` 변경 활동을 집계
- 검색을 켠 계정은 같은 `find` 출력으로 검색 DB도 갱신하여 두 번째 순회를 만들지 않음
- 금요일에는 삭제와 정확한 순증감을 보정하는 기준선 갱신을 시작
- 실패 또는 timeout 결과는 정상 기준선과 변경시각 cursor를 갱신하지 않음

Linux의 `ctime`은 생성시간이 아니라 inode 변경시간이고 birth time은 파일시스템에
따라 제공되지 않습니다. 따라서 일일 검사는 이식성이 있는 `mtime`을 사용합니다.
`find`도 파일 존재 여부를 확인하기 위해 트리를 순회해야 하므로 항상 즉시 끝나는
것은 아니지만, `du` 집계를 매일 반복하지 않고 출력도 스트리밍 처리합니다. 일일
byte는 변경된 파일의 현재 크기 합계이며 순증감은 주간 기준선에서 확정됩니다.
여러 밤짜리 기준선이 끝난 뒤 첫 변경 검사는 기준선 시작시각부터 다시 확인하므로,
그 사이 수정된 파일 활동도 누락하지 않습니다. 다만 실행 중 계속 변하는 파일시스템의
기준선은 스냅샷 시점 하나가 아니라 완료된 작업들을 합친 rolling baseline입니다.

서로 다른 top-level 경로 사이에 같은 inode를 가리키는 hard link가 있으면 분할된
`du` 작업의 합계가 실제 할당량보다 크게 보일 수 있습니다. 운영 파일시스템이 quota
명령을 제공한다면 계정별 실제 한도 표시는 quota 연동이 더 정확합니다.

이 값들은 데이터 디렉터리의 `accounts.json`에서 조정할 수 있습니다.

## 보고서 위치

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
data/reports/daily/YYYY-MM-DD.txt
data/reports/weekly/YYYY-MM-DD.txt
data/reports/cleanup/YYYY-MM-DD.txt
```

주간 보고서는 기본 금요일(`weekly_report_weekday: 4`)에 생성됩니다. 값은
Python의 요일 기준으로 월요일 0부터 일요일 6까지입니다.
정리 후보는 기본 100GB 이상, 최초 관찰 후 30일 경과, 최근 30일 top-level 변경
활동 없음 조건을 모두 만족해야 합니다. 이 보고서는 삭제 명령을 실행하지 않습니다.

## 테스트

```bash
python -m unittest discover -s tests -v
```

배포용 source tar에도 `tests/test_*.py`가 포함되므로 사내 Python에서 같은 회귀
테스트를 실행할 수 있습니다. 실제 운영 DB·계정 경로·로그는 포함되지 않습니다.

현재 설계 검토 내용과 남은 운영 고려사항은 [REVIEW.md](REVIEW.md), 기능 확장
우선순위는 [FEATURE_ROADMAP.md](FEATURE_ROADMAP.md)에 있습니다.
