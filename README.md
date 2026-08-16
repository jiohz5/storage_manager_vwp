# Storage Manager VWP

폐쇄망 RHEL/VWP에서 여러 프로젝트 계정 경로를 **읽기 전용으로** 모니터링하는
PyQt6 Fluent 데스크톱 애플리케이션입니다. 사용자가 명시적으로 지정한 데이터 디렉터리를 제외하면
모니터링 대상 계정에는 **파일을 쓰거나 삭제하지 않습니다.**

- 계정별 용량·inode 사용률 대시보드와 등급 표시 (정상 / 주의 / 경고 / 긴급 / FULL)
- 15분 주기 경량 수집과 FULL 도달 예상 시점 표시
- 야간(22:00~06:00) 상세 스캔으로 어느 경로가 늘었는지 분석
- 폐쇄망 안에서 동작하는 로컬 팝업 알림
- 일간·주간 보고서와 정리 후보 목록 (**자동 삭제는 하지 않습니다**)

설계 배경과 구현 이력은 [DESIGN.md](DESIGN.md)에 있습니다.

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
ls run.csh smvwp_cli.py smvwp
```

tar.gz를 받았다면:

```csh
tar -xzf storage_manager_vwp-main.tar.gz
cd storage_manager_vwp-main
ls run.csh smvwp_cli.py smvwp
```

마지막 `ls`에서 세 항목이 모두 보이면 올바른 디렉터리입니다.

> 반입용 아카이브에는 **실행에 필요한 것만** 들어 있습니다 (49개 파일, ZIP
> 약 150KB). 단위 테스트와 설계 문서는 반입 절차를 가볍게 하려고 제외했으며,
> 필요하면 [저장소](https://github.com/jiohz5/storage_manager_vwp)에서 전부
> 받을 수 있습니다.

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
경고를 기록할 수 있어야 하기 때문입니다.

이 변수를 생략하면 최초 GUI 실행 때 저장 경로를 묻는데, 이때 **쓸 수 있는
위치와 여유 공간을 함께 보여줍니다.** 고른 위치의 여유가 500MB 미만이면
경고합니다 (막지는 않습니다 — 폐쇄망에서는 선택지가 적을 수 있으니까요).

## 4. 진단 후 실행

```csh
chmod +x run.csh setup_cron.csh
./run.csh --diagnose      # 진단만
./run.csh                 # 진단 통과 시 GUI 실행
```

진단에서 확인하는 것:

| 항목 | 종합 판정 반영 |
|---|---|
| Python **3.12 이상** | 반영 |
| 표준 모듈 (`json`, `sqlite3`, `subprocess`, `dataclasses`, `uuid`, `argparse`, `threading`) | 반영 |
| 데이터 디렉터리 쓰기 권한 | 반영 (**미지정은 정상** — GUI가 최초 실행 때 묻습니다) |
| GUI 툴킷 (PyQt6 + qfluentwidgets) | **반영 안 함** — 없어도 수집 전용 CLI는 동작하므로 |

`종합 결과: OK`면 준비된 것입니다. GUI 툴킷이 없으면 종합은 `OK`라도 별도 줄로
"GUI는 실행할 수 없습니다"라고 표시되니 그 줄을 꼭 확인하세요.

GUI 툴킷이 없으면 무엇을 설치하면 되는지 안내가 나옵니다 (아래 "GUI" 절 참고).
Qt 설치가 어려운 장비라면 수집만 돌리고 화면은 다른 장비에서 열 수 있습니다.

GUI가 열리면 계정이 없을 때 **시작 안내**가 뜹니다. `계정 등록하기`를 눌러
계정명과 경로를 넣으세요 (예: `project_a` / `/user/project_a`).

등록할 때 그 경로의 **읽기 권한을 표본 조사**합니다. 읽을 수 없는 하위
디렉터리가 있으면 이렇게 알려줍니다.

```
확인한 디렉터리 200곳 중 37곳을 읽을 수 없습니다.
그 하위는 용량 측정에서 빠지므로 크기가 실제보다 작게 나옵니다.
```

관리자가 아니면 흔한 상황이라 **등록을 막지는 않습니다.** `df` 기반 사용률과
알림은 정상 동작하고, 상세 스캔의 크기만 **하한선**으로 읽으시면 됩니다.
표본에서 전체 비율을 추정하지는 않습니다 — 확인한 곳의 실제 개수만 알려줍니다.

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
| 매 15분 | `df`/inode 경량 수집 (`smvwp_cli.py collect`) |
| 매일 22:00 | 야간 상세 스캔 (`smvwp_cli.py scan`) — 06:00에 체크포인트 남기고 자동 정지 |

야간 스캔을 즉시 멈추려면 (강제 kill 아님, 다음 체크포인트에서 안전 정지):

```csh
$STORAGE_MANAGER_PYTHON_BIN smvwp_cli.py scan --data-dir "$STORAGE_MANAGER_DATA_DIR" --stop
```

## 6. 팝업 알림 등록 (선택)

메인 GUI와 **독립 실행**되는 트레이 알림기입니다. GUI를 닫아도 cron이 쌓은
경고를 띄웁니다.

```csh
# 로그인 시 자동 시작 등록
$STORAGE_MANAGER_PYTHON_BIN smvwp_cli.py notify --data-dir "$STORAGE_MANAGER_DATA_DIR" --install-autostart

# 지금 바로 실행 / 상태 확인 / 해제
$STORAGE_MANAGER_PYTHON_BIN smvwp_cli.py notify --data-dir "$STORAGE_MANAGER_DATA_DIR"
$STORAGE_MANAGER_PYTHON_BIN smvwp_cli.py notify --data-dir "$STORAGE_MANAGER_DATA_DIR" --status
$STORAGE_MANAGER_PYTHON_BIN smvwp_cli.py notify --data-dir "$STORAGE_MANAGER_DATA_DIR" --remove-autostart
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

좌측 네비게이션으로 화면을 오갑니다.

- **대시보드** — 최상단 요약(주의 이상 계정 수·가장 급한 계정), 계정 표
  (이름 · 경로 · 파일시스템 · 용량% · inode% · quota · 등급 · **FULL 예상** ·
  최근 수집 · 상태), 하단 상세 스캔 영역(시간창·남은 작업·증가 경로)
- **보고서** — 일간 / 주간 / 정리 후보
- **검색** — PIN 잠금 해제 후 이름 검색
- **설정** — 계정 관리 + 전역 설정
- 하단 언어 아이콘으로 한국어/English 즉시 전환 (재시작 불필요)

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
| `Could not load the Qt platform plugin "xcb"` | **Qt 6.5부터 `libxcb-cursor`가 새로 필요합니다** (Qt5에는 없던 요구사항이라 PyQt5로는 잘 뜨던 장비에서 처음 막힙니다). `./run.csh --diagnose`가 없는 라이브러리를 짚어 줍니다. 관리자: `yum install xcb-util-cursor libxkbcommon-x11` / 비관리자: RPM만 받아 홈에 풀고 `LD_LIBRARY_PATH`에 추가 |
| GUI가 안 뜸 | 실행하면 무엇을 설치하면 되는지 안내가 나옵니다. Fluent를 쓰려면 `python -m pip install PyQt6 "PyQt6-Fluent-Widgets[full]" pyqtdarktheme` |
| Fluent 대신 클래식이 뜸 | Fluent 패키지가 없거나 import에 실패한 것입니다. `./smvwp_cli.py gui6`로 강제 실행하면 원인이 그대로 보입니다 |
| 진단에 `데이터 디렉터리: 미지정` | 정상입니다. GUI가 최초 실행 때 물어봅니다 (cron을 쓸 거면 미리 지정 필요) |
| inode가 `확인불가` | 일부 파일시스템(NFS 등)이 inode를 보고하지 않음 — 정상 |
| 요약줄에 `최근 24시간 중 N%만 수집됐습니다` | **cron이 안 돌고 있을 가능성이 높습니다.** `crontab -l`로 등록을 확인하고, 없으면 `./setup_cron.csh` 재실행. 등록돼 있는데도 그렇다면 사내에서 cron 사용이 제한됐을 수 있습니다(관리자 문의) — 그동안은 GUI를 켜 두면 내부 타이머가 대신 수집합니다 |
| 요약줄에 `수집이 멈춰 있습니다` | 수집기가 죽었거나 데이터 경로에 쓸 수 없는 상태. `<data_dir>/logs/*.log` 확인 |
| 증가 경로에 `읽을 수 없는 하위 디렉터리` 경고 | 권한이 없는 폴더가 섞여 있어 크기가 실제보다 작게 측정됐다는 뜻입니다. 값은 유효하지만 **하한선**으로 보세요 |
| cron이 안 도는 것 같음 | `crontab -l`, `<data_dir>/logs/*.log` 확인 |
| 야간 스캔이 `paused` | 정상 — 06:00 시간창 종료로 안전 정지, 다음 밤에 이어서 진행 |

## GUI (PyQt6 Fluent)

화면은 Windows 11 Fluent 스타일이며 좌측 네비게이션으로 대시보드·보고서·
검색·설정을 오갑니다. 다크 테마가 기본입니다.

### 설치

```sh
python -m pip install PyQt6 "PyQt6-Fluent-Widgets[full]" pyqtdarktheme
```

`PyQt6 6.11`은 최신 VC++ 런타임을 요구해 환경에 따라 `DLL load failed`가
납니다. 그런 경우 검증된 조합으로 내려 쓰세요.

```sh
python -m pip install "PyQt6==6.7.1" "PyQt6-Qt6==6.7.3" "PyQt6-sip==13.8.0"
```

**리눅스에서는 시스템 라이브러리도 필요합니다.** Qt 6.5부터 `libxcb-cursor`가
새로 요구되는데 Qt5에는 없던 것이라, PyQt5로는 잘 뜨던 장비에서도 이것 때문에
막힐 수 있습니다.

```sh
sudo yum install xcb-util-cursor libxkbcommon-x11   # RHEL
```

관리자 권한이 없다면 RPM만 받아 홈에 풀고 경로를 잡아도 됩니다.

```sh
mkdir -p ~/qtlibs && cd ~/qtlibs
rpm2cpio xcb-util-cursor-*.rpm | cpio -idmv
setenv LD_LIBRARY_PATH ~/qtlibs/usr/lib64:$LD_LIBRARY_PATH
```

`./run.csh --diagnose`가 무엇이 없는지 짚어 주므로 먼저 돌려 보세요.

> **수집·스캔은 GUI 없이도 돕니다.** `smvwp_cli.py collect`와
> `smvwp_cli.py scan`은 Qt를 전혀 import하지 않으므로, 화면이 없는 장비나
> cron에서도 그대로 동작합니다. Qt 설치가 어려운 장비는 수집만 돌리고 화면은
> 다른 장비에서 같은 데이터 경로로 열면 됩니다.

## 테스트

단위 테스트는 반입용 아카이브에서 제외되어 있습니다. 저장소를 통째로 받은
경우에는 Qt 없이도 GUI를 제외한 전 로직을 검증할 수 있습니다.

```csh
$STORAGE_MANAGER_PYTHON_BIN -m unittest discover -s tests -t .
```
