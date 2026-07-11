# Storage Manager VWP

폐쇄망 RHEL/VWP에서 여러 프로젝트 계정 경로를 읽기 전용으로
모니터링하는 PyQt5 애플리케이션입니다. 모니터링 대상 계정에는 파일을 쓰거나
삭제하지 않습니다.

## 주요 기능

- 여러 프로젝트 계정 추가, 수정, 비활성화 및 삭제
- `df -Pk/-Pi <account-path>` 기반의 byte·inode 사용률 대시보드
- 선택적 quota JSON command 연동과 공유 filesystem 계정 그룹화
- 사용률 상태: 90% 미만 정상, 90~94% 주의(WARN), 95% 이상 경고(ALERT)
- 사용률 색상: 80% 미만 녹색, 80~89% 노란색, 90~94% 주황색,
  95% 이상 빨간색 및 통합 팝업 경고
- GUI가 종료되어도 매일 22시에 실행되는 cron 야간 작업
- `추적` 탭에서 cron 등록, 다음 실행, 현재 PID, 대상 계정 및 최근 결과 확인
- GUI와 독립된 지금 실행, 체크포인트 보존 안전 중지, 중지 후 재시작
- 상단 `언어` 메뉴에서 한국어(KOR)/영어(ENG) 즉시 전환
- 일간 보고서와 금요일 주간 보고서
- 7일·30일 추세의 경고/100% 예상일과 평소 대비 급증 이상 탐지
- 07시 건강 점검 cron을 통한 수집 누락·실패 알림
- 검토 전용 정리 후보 보고서 (자동 삭제 없음)
- GUI 없이 cron에서 동작하는 outbox·사내 command·내부 webhook 알림
- 최대 1년간의 일별 사용률 추이
- 여러 밤에 걸쳐 재개되는 기준선 `du`와 일일 변경 파일 활동 확인
- SQLite, JSON, 텍스트 보고서만 사용하는 로컬 저장

## 폐쇄망 반입

애플리케이션 소스에는 Python 런타임이나 wheel을 포함하지 않습니다. 따라서
압축 파일이 작고, 사내에 이미 설치된 Python 3.10과 PyQt5를 그대로 사용합니다.

RHEL에서 압축을 해제한 뒤 csh에서 Python 홈만 지정하면 됩니다.

```csh
setenv STORAGE_MANAGER_PYTHON_HOME /path/to/python-3.10
chmod +x run.csh setup_cron.csh
./run.csh
```

기본 데이터 위치는 압축을 푼 디렉터리의 `data/`입니다. 소스를 개인 1GB 홈에
둘 경우에는 데이터만 용량이 충분한 위치로 보냅니다.

```csh
setenv STORAGE_MANAGER_DATA_DIR /large/path/storage-manager-data
./run.csh
```

두 환경변수를 로그인 때마다 유지하려면 개인 `.cshrc`에 `setenv` 행을 넣습니다.

## 환경 확인

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 verify_environment.py \
  --data-dir ./data
```

별도 데이터 경로를 지정했다면 `./data` 대신 `$STORAGE_MANAGER_DATA_DIR`을
사용합니다.

점검기는 앱과 동일한 명령을 작은 임시 디렉터리에서 실제 실행합니다. 다음 핵심
항목이 모두 `OK`여야 하며 `WARN`은 내용을 확인한 뒤 진행합니다.

- Python 3.8 이상 (권장 및 개발 기준: 3.10)
- PyQt5 import 및 Qt 화면 플랫폼 플러그인 초기화
- SQLite 3.24 이상 및 실제 DB 생성
- 앱이 사용하는 옵션 그대로 실행한 `df -Pk`, `df -Pi`, `du` probe
- RHEL 명령 `find`, `crontab`, `csh`
- `/user` 읽기 권한과 데이터 디렉터리 쓰기 권한
- 데이터 파일시스템 종류와 남은 용량

데이터 위치가 NFS, CIFS, Lustre 등으로 탐지되면 경고가 표시됩니다. 이 경우에도
관리자 한 명의 전용 데이터 디렉터리로는 사용할 수 있지만, 여러 관리자가 같은
SQLite 파일을 동시에 공유하면 안 됩니다.

추가 패키지나 인터넷 연결은 필요하지 않습니다. 그래프도 matplotlib 대신
PyQt5 자체 그리기를 사용합니다.

## 실행과 계정 등록

```csh
./run.csh
```

`Accounts` 탭에서 계정 이름과 `/user/account-name`을 등록합니다. 경로에는
계정명만 입력해도 `/user/`가 자동으로 붙습니다. 안전을 위해 존재하고 읽을 수
있는 `/user` 바로 아래 디렉터리만 등록됩니다.

경로 입력란에는 절대경로를 직접 입력할 수 있습니다. 허용할 상위 경로는
`accounts.json`의 `monitored_roots` 목록으로 관리하며 운영 기본값은
`["/user"]`입니다. 목록 밖의 경로나 상위 경로 자체는 등록되지 않습니다.

직접 실행하려면 다음과 같습니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 app.py \
  --data-dir /large/path/storage-manager-data
```

## 22시·07시 cron 등록

cron은 계속 떠 있는 daemon이 아닙니다. 매일 22시에 `nightly_scan.py`를 실행하고,
매일 07시에 `health_check.py`가 전날 수집 누락·실패를 확인합니다. 따라서 `추적`
탭은 `cron 등록 후 대기`와 `실제 스캔 실행 중`을 별도 상태로 표시합니다.

GUI의 `Setup` 탭에서 `Install/update 22:00 cron`을 누르거나 다음 명령을 한 번
실행합니다.

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

`추적` 탭의 `지금 백그라운드 실행`은 GUI를 닫아도 계속됩니다. `안전 중지`는 현재
실행의 run ID에만 요청을 보내고, 실행 중인 `du/find`를 정리한 뒤 완료한 디렉터리
체크포인트를 남깁니다. 임의 PID에 강제 signal을 보내지 않으므로 다른 사용자의
프로세스를 종료하지 않습니다. `중지 후 재시작`의 자동 재시작은 GUI가 열려 있을
때 수행됩니다.

## Cron 내부 알림

설정 탭의 `cron 알림 모드`는 다음 중 하나입니다. 어떤 모드든 알림 원문과 전송
결과는 데이터 디렉터리에 감사 기록으로 남습니다.

- `파일 outbox`: 기본값. `data/notifications/*.json`만 생성하며 외부 전송은 하지 않음
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

GUI의 `테스트 알림`으로 연결을 먼저 확인합니다. 22시 수집은 byte/inode/quota
주의·경고, 급증 이상, Full 임박을 전송합니다. 07시 건강 점검은 30시간 이상 오래된
수집과 최근 비정상 종료를 전송합니다. 동일 key·동일 등급은 기본 12시간 억제하며,
주의에서 경고로 상승하면 cooldown 중에도 즉시 다시 전송합니다.

## Quota 어댑터

스토리지별 quota 출력 형식이 다르므로 설정 탭에 shell 없는 command argv를 JSON
배열로 지정합니다. `{account}`와 `{path}`가 치환되며 command는 다음 JSON을 stdout에
출력해야 합니다.

```json
{"used_kb": 950000, "limit_kb": 1000000, "soft_limit_kb": 900000}
```

예: `["/opt/company/bin/quota-json", "{account}", "{path}"]`. 설정하지 않으면 quota
열만 `-`로 표시되고 byte·inode 수집은 계속됩니다.

## 데이터 크기 제한

- GUI를 몇 번 새로고침해도 계정별 GUI 스냅샷은 하루 1행만 갱신됩니다.
- 야간 스냅샷도 계정별 하루 1행입니다.
- 상세 이력과 보고서는 기본 365일 후 삭제됩니다.
- cron 로그는 5MB가 되면 회전하며 3개까지만 보관합니다.
- 현재 top-level 전체 기준선은 계정별 1세트만 유지합니다.
- 초기/주간 기준선의 체크포인트는 파일이 아니라 디렉터리 작업만 저장합니다.
- 일일 변경 검사는 파일 경로 전체를 저장하지 않고 top-level 집계만 저장합니다.
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

- 계정별 상세 제한시간: 기본 1시간
- 개별 `du` 디렉터리 작업 제한시간: 기본 15분
- 허용시간: 22:00~06:00, 안전 종료 05:45
- 전체 예산: 최대 8시간이지만 실제 시작시각부터 05:45까지 남은 시간만 사용
- 예산이 부족하면 날짜별 시작 계정을 순환해 특정 계정만 계속 제외되지 않게 함
- 95% 이상 계정을 상세 스캔에서 우선 처리
- 초기 기준선은 완료된 디렉터리 작업을 SQLite에 저장하고 다음 밤에 이어서 수행
- 큰 디렉터리가 timeout되면 한 단계 하위 디렉터리 작업으로 나누어 재시도
- 완료 직전 top-level 목록을 다시 대조해 스캔 중 생성·삭제된 항목을 작업표에 반영
- 기준선과 변경 cursor를 DB에 기록한 뒤에만 체크포인트를 제거해 중단 후 재실행 안전
- 초기 기준선 이후에는 `find -newermt`로 `mtime + 현재 byte` 변경 활동을 집계
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

현재 설계 검토 내용과 남은 운영 고려사항은 [REVIEW.md](REVIEW.md), 기능 확장
우선순위는 [FEATURE_ROADMAP.md](FEATURE_ROADMAP.md)에 있습니다.
