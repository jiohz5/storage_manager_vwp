# Storage Manager VWP

폐쇄망 RHEL/VWP에서 여러 프로젝트 계정 경로를 **읽기 전용으로** 모니터링하는
PyQt5 애플리케이션입니다. 사용자가 명시적으로 지정한 데이터 디렉터리를 제외하면
모니터링 대상 계정에는 **파일을 쓰거나 삭제하지 않습니다.**

- 계정별 용량·inode 사용률 대시보드와 등급 표시 (정상 / 주의 / 경고 / 긴급 / FULL)
- 15분 주기 경량 수집과 FULL 도달 예상 시점 표시
- 야간(22:00~06:00) 상세 스캔으로 어느 경로가 늘었는지 분석
- 폐쇄망 안에서 동작하는 로컬 팝업 알림
- 일간·주간 보고서와 정리 후보 목록 (**자동 삭제는 하지 않습니다**)

설계 배경은 [DESIGN.md](DESIGN.md), [CONCEPT.md](CONCEPT.md),
[REBUILD_CONCEPT.md](REBUILD_CONCEPT.md)에 있습니다.

---

# 설치: 다운로드부터 실행까지

소스 코드를 편집할 필요는 없습니다. 압축을 푼 뒤 csh에서 **환경변수 두 개**만
지정하면 됩니다.

> **cron에는 소스 디렉터리의 절대경로가 기록됩니다.** 폴더를 옮길 계획이라면
> cron을 등록하기 전에 최종 위치로 먼저 옮기세요.

## 1. Windows/OA PC에서 내려받기

VWP는 폐쇄망이라 그 안에서 `git clone`을 할 수 없습니다. 인터넷이 되는
Windows/OA PC에서 받아 반입해야 합니다.

[storage_manager_vwp 저장소](https://github.com/jiohz5/storage_manager_vwp)를 열고
`main` 브랜치에서 `Code` → `Download ZIP`을 누르면
`storage_manager_vwp-main.zip`을 받을 수 있습니다. 이 파일 하나를 **승인된 사내
반입 절차**로 VWP/RHEL에 옮깁니다.

RHEL에 `unzip`이 없다면
[main tar.gz](https://github.com/jiohz5/storage_manager_vwp/archive/refs/heads/main.tar.gz)를
대신 받아 옮깁니다.

인터넷이 되는 테스트 환경이라면 아래도 가능합니다.

```sh
git clone --depth 1 https://github.com/jiohz5/storage_manager_vwp.git
cd storage_manager_vwp
```

## 2. RHEL에서 압축 해제

```csh
cd /path/to/transfer-directory
unzip storage_manager_vwp-main.zip
cd storage_manager_vwp-main
ls run.csh app.py smvwp
```

tar.gz를 받았다면:

```csh
tar -xzf storage_manager_vwp-main.tar.gz
cd storage_manager_vwp-main
ls run.csh app.py smvwp
```

마지막 `ls`에서 세 항목이 모두 보이면 올바른 디렉터리입니다.

## 3. 환경변수 두 개 지정

```csh
# (1) Python 3.12 실행 파일 - 실행 파일 자체를 가리켜야 합니다
setenv STORAGE_MANAGER_PYTHON_BIN /installed/python/3.12.x/bin/python3

# (2) 수집 데이터를 둘 여유 있는 전용 경로
setenv STORAGE_MANAGER_DATA_DIR /large/private/path/storage-manager-data
mkdir -p "$STORAGE_MANAGER_DATA_DIR"
chmod 700 "$STORAGE_MANAGER_DATA_DIR"

# PYTHONHOME은 이 앱의 변수가 아닙니다. 설정돼 있으면 해제하세요.
if ($?PYTHONHOME) unsetenv PYTHONHOME
```

**Python 경로 주의**: 설치 prefix(`/installed/python/3.12.x`)가 아니라 그 아래의
**실행 파일**(`.../bin/python3`)을 지정합니다. 이 앱은
`STORAGE_MANAGER_PYTHON_HOME` 방식을 지원하지 않습니다 — 옵션이 하나뿐이라
헷갈릴 여지가 없습니다.

**데이터 경로 주의**: 이 경로는 현재 사용자와 **cron 모두** 쓸 수 있어야 합니다.
모니터링 대상과 **다른 파일시스템**을 권장합니다 — 대상이 FULL이 되어도 DB와
경고를 기록할 수 있어야 하기 때문입니다. 이 변수를 생략하면 최초 GUI 실행 때
저장 경로를 한 번 묻습니다.

## 4. 진단 후 실행

```csh
chmod +x run.csh setup_cron.csh
./run.csh --diagnose      # 진단만
./run.csh                 # 진단 통과 시 GUI 실행
```

진단에서 다음이 모두 `OK`여야 합니다.

- Python **3.12 이상**
- 표준 모듈 (`json`, `sqlite3`, `subprocess`, `dataclasses`, `uuid`, `argparse`, `threading`)
- PyQt5 import
- 데이터 디렉터리 쓰기 권한

GUI가 열리면 계정이 없을 때 **시작 안내**가 뜹니다. `계정 등록하기`를 눌러
계정명과 경로를 넣으세요 (예: `project_a` / `/user/project_a`).

## 5. 자동 수집 등록 (선택이지만 권장)

GUI를 계속 띄워 두면 내부 타이머가 15분마다 수집합니다. **GUI를 닫아도**
수집이 계속되게 하려면 cron에 등록합니다.

```csh
./setup_cron.csh
crontab -l | grep storage_manager_vwp_v2
```

두 줄이 등록됩니다.

| 시각 | 하는 일 |
|---|---|
| 매 15분 | `df`/inode 경량 수집 (`collector_cli.py`) |
| 매일 22:00 | 야간 상세 스캔 (`nightly_scan_cli.py`) — 06:00에 체크포인트 남기고 자동 정지 |

야간 스캔을 즉시 멈추려면 (강제 kill 아님, 다음 체크포인트에서 안전 정지):

```csh
$STORAGE_MANAGER_PYTHON_BIN nightly_scan_cli.py --data-dir "$STORAGE_MANAGER_DATA_DIR" --stop
```

## 6. 팝업 알림 등록 (선택)

메인 GUI와 **독립 실행**되는 트레이 알림기입니다. GUI를 닫아도 cron이 쌓은
경고를 띄웁니다.

```csh
# 로그인 시 자동 시작 등록
$STORAGE_MANAGER_PYTHON_BIN notifier_cli.py --data-dir "$STORAGE_MANAGER_DATA_DIR" --install-autostart

# 지금 바로 실행 / 상태 확인 / 해제
$STORAGE_MANAGER_PYTHON_BIN notifier_cli.py --data-dir "$STORAGE_MANAGER_DATA_DIR"
$STORAGE_MANAGER_PYTHON_BIN notifier_cli.py --data-dir "$STORAGE_MANAGER_DATA_DIR" --status
$STORAGE_MANAGER_PYTHON_BIN notifier_cli.py --data-dir "$STORAGE_MANAGER_DATA_DIR" --remove-autostart
```

## 7. 다음 로그인에도 유지

정상 동작을 확인한 뒤 개인 `~/.cshrc`에 두 줄을 넣으면 로그인할 때마다 다시
입력하지 않아도 됩니다.

```csh
setenv STORAGE_MANAGER_PYTHON_BIN /installed/python/3.12.x/bin/python3
setenv STORAGE_MANAGER_DATA_DIR /large/private/path/storage-manager-data
```

`PYTHONHOME`은 `.cshrc`에 넣지 마세요. 소스 폴더를 나중에 옮기면 기존 cron이
옛 절대경로를 계속 가리키므로 **새 위치에서 `./setup_cron.csh`를 다시
실행**해야 합니다.

---

# 운영

## 화면 구성

대시보드 한 화면에 모두 있습니다 (탭 없음).

- **최상단 요약**: 주의 이상 계정 수와 가장 급한 계정
- **계정 표**: 이름 · 경로 · 파일시스템 · 용량% · inode% · quota · 등급 ·
  **FULL 예상** · 최근 수집 · 상태
- **하단 상세 스캔 영역**: 야간 시간창 상태, 남은 작업 수, 선택 계정의 증가 경로
- 버튼: `지금 수집` · `계정 관리/설정` · `보고서` · `검색` · `진단`
- 상단 `언어` 메뉴에서 한국어/English 즉시 전환 (재시작 불필요)

## FULL 예상 컬럼 읽는 법

| 표시 | 뜻 |
|---|---|
| `1시간 이내`, `약 5시간` | 임박 — 최근 3시간 추세 기준 |
| `약 12일 / 약 45일` | 7일 추세 기준 / 30일 추세 기준 |
| `예측 불가(표본 부족)` | 아직 표본이 모자람 |
| `예측 불가(증가 추세 아님)` | 줄고 있거나 정체 |
| `예측 불가(예측 범위 초과)` | 증가가 미미해 의미 있는 예측 불가 |

**숫자를 지어내지 않습니다.** 근거가 부족하면 그럴듯한 값 대신 사유를 표시합니다.
`df`는 계정 단독이 아니라 그 경로가 속한 **파일시스템 전체** 사용량입니다.

## 알림

기본은 **파일 outbox**(네트워크 불필요)입니다. `계정 관리/설정`에서 바꿀 수
있습니다.

| 모드 | 동작 |
|---|---|
| 파일 outbox (기본) | `<data_dir>/outbox/`에 JSON 기록 → 트레이 알림기가 표시 |
| 사내 command | 지정한 프로그램을 shell 없이 실행하고 UTF-8 JSON을 stdin으로 전달 |
| 내부 webhook | 사내 HTTP(S) 주소로 UTF-8 JSON POST |
| 사용 안 함 | 생성·전송 모두 중지 |

어떤 모드든 **원문과 전송 결과가 `<data_dir>/notify_audit/`에 남습니다.**
command/webhook은 JSON 배열/URL 형식이라 설정 화면에서 형식을 검사합니다.

## 데이터 디렉터리 구성

```
<data_dir>/
  config.json            # 계정 목록 + 전역 설정
  samples.db              # 15분 표본 (기본 90일 보존)
  detail_scan.db           # 야간 스캔 체크포인트·기준선·증감 이력
  search_index.db           # 이름 검색 인덱스 (켠 계정만)
  outbox/                    # 알림 JSON
  notify_audit/               # 알림 감사 기록
  reports/                     # 일간·주간·정리후보 (언어별)
  logs/                         # cron 로그
```

## 관리자 검색

`검색` 버튼 → PIN 입력. **기본 PIN은 `6368`이며 첫 사용 후 반드시 변경하세요**
(검색 화면의 `PIN 변경...`). 기본 PIN을 쓰는 동안 경고가 표시됩니다.

PIN은 **화면 노출 제한**이지 보안 경계가 아닙니다 — 검색 DB는 암호화되지 않으며
파일을 직접 읽을 수 있는 사람은 PIN과 무관하게 내용을 볼 수 있습니다. 실제 접근
통제는 데이터 디렉터리의 파일시스템 권한으로 하세요.

검색 인덱싱은 **계정별로 켜야** 합니다(기본 꺼짐). 파일 **이름·확장자·경로만**
저장하며 내용은 저장하지 않습니다.

## 문제 해결

| 증상 | 확인 |
|---|---|
| `STORAGE_MANAGER_PYTHON_BIN이 설정되지 않았습니다` | 3단계 환경변수 지정 |
| `지정한 Python 실행 파일을 실행할 수 없습니다` | 경로가 prefix가 아닌 `bin/python3`인지, 실행 권한이 있는지 |
| 진단에서 Python 버전 FAIL | 3.12 이상 경로인지 (`$STORAGE_MANAGER_PYTHON_BIN -V`) |
| PyQt5 사용 불가 | 해당 Python 설치에 PyQt5가 있는지 (`$STORAGE_MANAGER_PYTHON_BIN -c "from PyQt5 import QtWidgets"`) |
| inode가 `확인불가` | 일부 파일시스템(NFS 등)이 inode를 보고하지 않음 — 정상 |
| cron이 안 도는 것 같음 | `crontab -l`, `<data_dir>/logs/*.log` 확인 |
| 야간 스캔이 `paused` | 정상 — 06:00 시간창 종료로 안전 정지, 다음 밤에 이어서 진행 |

## 테스트

PyQt5 없이도 GUI를 제외한 전 로직을 검증할 수 있습니다.

```csh
$STORAGE_MANAGER_PYTHON_BIN -m unittest discover -s tests -t .
```
