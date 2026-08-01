# Storage Manager VWP - 재구현판 (phase 1)

이 디렉터리는 저장소 루트의 [`CONCEPT.md`](../CONCEPT.md) /
[`REBUILD_CONCEPT.md`](../REBUILD_CONCEPT.md)에서 정리한 설계를 바탕으로
**완전히 새로 작성한** 구현이다. 저장소 루트의 `storage_manager/`, `app.py`
등 기존 Python 3.10 / PyQt5 구현은 그대로 두고 참고용으로만 남겨 두었으며,
이 디렉터리 코드는 그것을 옮긴 것이 아니라 새로 쓴 것이다.

## 왜 새 디렉터리인가

기존 구현과 새 구현이 같은 저장소 안에 당분간 공존해야 하기 때문에, 이름과
위치를 명확히 분리했다:

- 기존: 저장소 루트의 `storage_manager/`, `app.py`, `run.csh` 등
- 신규: 이 디렉터리(`storage_manager_v2/`) 전체가 독립적인 애플리케이션이다
  (자체 `run.csh`, 자체 `app.py`, 자체 Python 패키지 `smvwp`)

Python 패키지 이름도 기존 `storage_manager`와 겹치지 않도록 `smvwp`
(Storage Manager VWP)로 새로 지었다 - 혹시 두 코드베이스가 같은
`PYTHONPATH`에 함께 놓이는 상황이 생겨도 import가 섞이지 않게 하기 위함.

## 실행 방법

```
cd storage_manager_v2
setenv STORAGE_MANAGER_PYTHON_BIN /installed/python/3.12.x/bin/python3
./run.csh                 # 사전 점검 후 바로 GUI 실행
./run.csh --diagnose      # 진단만 하고 종료 (Python/PyQt5/데이터 디렉터리 점검)
```

기존 구현과 달리 `STORAGE_MANAGER_PYTHON_HOME`은 지원하지 않는다
(REBUILD_CONCEPT.md 3절 결정 - 옵션이 하나면 헷갈릴 여지가 없다).

데이터 디렉터리(`STORAGE_MANAGER_DATA_DIR`)를 지정하지 않으면 최초 실행 시
GUI가 한 번 물어보고, 이후에는 홈 디렉터리의 작은 포인터 파일
(`~/.storage_manager_vwp/data_dir`)에 기억해 둔다. 배포 폴더 자체는 반입
절차상 다시 덮어써질 수 있어 포인터를 그 안에 두지 않았다.

15분 주기 수집은 두 가지 경로를 모두 지원한다:

- GUI를 띄워 두면 내부 타이머(`smvwp/scheduler.py`)가 자동으로 돈다.
- GUI 없이도 계속 수집하려면 `./setup_cron.csh`로 cron에
  `collector_cli.py`를 등록한다 (`STORAGE_MANAGER_DATA_DIR`을 미리
  설정해야 한다 - cron은 대화형 세션이 아니므로 포인터 파일에 의존하지 않고
  명시적으로 받는다).

## 디렉터리 구조

```
storage_manager_v2/
  run.csh              # 단일 진입점 (진단 + 실행)
  setup_cron.csh        # 15분 주기 수집 cron 등록 도우미
  app.py                # GUI 진입점
  collector_cli.py       # cron용 1회 수집 스크립트 (PyQt5 미의존)
  smvwp/                  # 애플리케이션 패키지
    tiers.py               # 사용률 등급 계산 (정상/주의/경고/긴급/FULL)
    paths.py                 # 데이터 디렉터리 탐색/기억/쓰기 안전성
    config.py                 # JSON 설정 (계정 목록 + 전역 설정)
    collector.py               # df/inode 조회 및 파싱
    store.py                    # SQLite 표본 저장소 (보존기간 정리 포함)
    notifications.py             # 파일 outbox 알림 + cooldown
    cycle.py                      # 수집->저장->알림 한 사이클 (GUI/cron 공유)
    scheduler.py                   # GUI 내부 QTimer 주기 실행 + 스캔 워커 (PyQt5 의존)
    diagnostics.py                  # Python/모듈/PyQt5/데이터 디렉터리 진단
    scan_window.py                   # 22:00~06:00 시간창 계산
    scan_lock.py                      # 실행 잠금 + run ID 매칭 안전 중지
    scan_store.py                      # 스캔 체크포인트/기준선 SQLite 저장소
    detail_scan.py                      # du 기반 기준선 스캔 (분할/우선순위)
    activity_scan.py                     # find -newermt 기반 변경 파일 스캔
    nightly_scan.py                       # 야간 스캔 오케스트레이터
    gui/                                    # PyQt5 화면 (PyQt5 의존)
      main_window.py                         # 대시보드 단일 화면 (+ 상세 스캔 영역)
      account_dialog.py                       # 계정 등록/설정 다이얼로그
      widgets.py                               # 등급 배지 / 크기 표기 위젯
  tests/                # unittest 기반 단위 테스트 (PyQt5 불필요)
```

**모듈 분리 원칙**: `smvwp` 최상위와 `gui/`, `scheduler.py`를 의도적으로
나눴다. `tiers`/`paths`/`config`/`collector`/`store`/`notifications`/`cycle`/
`diagnostics`는 PyQt5를 전혀 import하지 않으므로, PyQt5가 없는 환경(예: 이
저장소를 검토만 하는 개발 PC, 또는 cron 환경)에서도 그대로 단위 테스트하고
재사용할 수 있다. PyQt5 의존은 `smvwp/gui/*`와 `smvwp/scheduler.py`에만
있다.

이 구조는 이번 작업에서 임의로 정한 것이며 (REBUILD_CONCEPT.md 9절
"모듈 분리 방식은 아직 열린 질문"), 다음 세션에서 사람이 바꾸기 쉽도록 각
모듈의 책임을 최대한 좁게 나눠 두었다.

## 데이터 디렉터리 레이아웃

모니터링 대상 계정 경로에는 절대 쓰지 않는다 - 오직 아래 데이터 디렉터리
안에만 쓴다 (CONCEPT.md 1절의 읽기 전용 불변식).

```
<data_dir>/
  config.json          # 계정 목록 + 전역 설정 (JSON)
  samples.db            # df/inode 표본 이력 (SQLite)
  outbox/                 # 알림 JSON 파일들
  notify_state.json        # 알림 cooldown 상태
```

## 테스트

PyQt5 없이도 `smvwp.gui`/`smvwp.scheduler`를 제외한 모든 로직을 테스트할 수
있다:

```
cd storage_manager_v2
python3 -m unittest discover -s tests -t .
```

## 구현 범위와 아직 안 만든 것

**phase 1** (REBUILD_CONCEPT.md 7절): 계정 등록 + df 기반 대시보드, 사용률
등급 표시(색상+텍스트), 15분 경량 수집(cron/타이머), 파일 outbox 알림, 최소
진단, 대시보드 단일 화면 + 한눈 요약.

**phase 2** (REBUILD_CONCEPT.md 8절 1번): 야간 상세 스캔. CONCEPT.md 3·7절의
검증된 원칙을 계승하되 코드는 새로 썼다.

- 22:00~06:00 시간창 안에서만 cron 실행 (`scan_window.py`). 06:00이 되면 강제
  종료가 아니라 완료된 체크포인트를 남기고 `paused` 상태로 스스로 멈춘다.
- 터미널 직접 실행(`nightly_scan_cli.py --now`)은 의도적 진단/복구 경로라
  시간창 제한을 받지 않는다.
- 디렉터리 단위 체크포인트로 완전히 재개 가능 (`scan_store.py`) - 중단된
  다음 실행은 이미 끝낸 디렉터리를 다시 훑지 않는다.
- 강제 kill 없음: run ID 매칭 중지 요청 파일만 사용 (`scan_lock.py`).
  `nightly_scan_cli.py --stop` 또는 GUI의 `안전 중지` 버튼.
- 디렉터리 단위 타임아웃 초과 시 하위 디렉터리 작업으로 분할해 재시도.
- 기준선 비교는 "완료된 세대 vs 직전 완료 세대"를 **같은 경로끼리** 대조한다
  (기존 REVIEW.md가 지적한 "어제 top N vs 오늘 top N" 비교 오류를 피함).
- `nice`/`ionice`는 있으면 쓰고 없으면 그냥 진행 (최선 노력이지 처리량 상한이
  아님).
- 대시보드 아래쪽에 상세 스캔 영역을 추가 (탭 신설 없이 단일 화면 유지) -
  시간창/실행 상태/남은 작업 수와 선택 계정의 증가 경로를 보여준다. 전체
  분모를 모르는 진행률은 퍼센트로 부풀리지 않고 남은 작업 수로만 표시한다.

**아직 만들지 않은 것** (다음 단계 후보, 지금은 의도적으로 비워둠):

- 알림 채널 다양화(command/webhook), 검색 인덱스, quota 연동, 다국어(KOR/ENG)
  UI, 정리 후보(cleanup candidates) 보고서, 최초 실행 마법사, 클릭 수 최소화
  단축 버튼 등 GUI 사용성 개선 후보들.
- 트레이 알림기(notifier) 프로세스 자체 - outbox JSON 파일 포맷은 갖춰
  뒀지만, 그 파일을 읽어 팝업을 띄우는 별도 프로세스는 아직 없다.
- 상세 스캔 결과를 알림/보고서로 내보내는 경로 (지금은 GUI 표시 전용).

## 알려진 제약 / 로컬 개발 환경 메모

- 단위 테스트는 Windows + Python 3.10에서도 전부 통과한다 (PyQt5 없이도
  동작하도록 GUI 의존을 분리해 뒀기 때문). Git Bash가 있는 Windows에서는
  `df`/`du`/`find`도 있어서 GUI 실행, 15분 수집, 상세 스캔(`--now`)까지
  실제로 돌려 확인했다. 다만 `df -Pi`가 NTFS에서 inode를 보고하지 않아
  inode 등급은 `확인불가`로 표시된다 - 숫자를 지어내지 않는 정상 동작이다.
- 실제 VWP(RHEL, Python 3.12)에서 재확인이 필요한 것: Python 3.12 + PyQt5
  조합, `crontab` 등록 권한, `nice`/`ionice` 존재 여부, 대용량 계정에서의
  실제 스캔 소요 시간과 06:00 인계 동작.
- `run.csh` / `setup_cron.csh`는 csh 스크립트라 Windows에서 직접 실행/검증할
  수 없었다 - 로직은 기존 루트 `run.csh`의 검증된 패턴(진단 실패 시 exit 2,
  `PYTHONHOME` 경고 후 제거 등)을 그대로 계승했다.
