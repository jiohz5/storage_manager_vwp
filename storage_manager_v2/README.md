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
    scheduler.py                   # GUI 내부 QTimer 기반 주기 실행 (PyQt5 의존)
    diagnostics.py                  # Python/모듈/PyQt5/데이터 디렉터리 진단
    gui/                              # PyQt5 화면 (PyQt5 의존)
      main_window.py                   # 대시보드 단일 화면
      account_dialog.py                 # 계정 등록/설정 다이얼로그
      widgets.py                         # 등급 배지 위젯
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

## phase 1 범위와 아직 안 만든 것

phase 1 범위는 저장소 루트 `REBUILD_CONCEPT.md` 7절을 그대로 따른다:
계정 등록 + df 기반 대시보드, 사용률 등급 표시(색상+텍스트), 15분 경량 수집
(cron/타이머), 파일 outbox 알림, 최소 진단, 대시보드 단일 화면 + 한눈 요약.

**아직 만들지 않은 것** (REBUILD_CONCEPT.md 8절 순서대로 다음 단계에서
추가 예정, 지금은 의도적으로 비워둠):

- 야간 상세 스캔 (`du`/`find` 기반 계정별 증가 경로 분석) - 다음 순서 1번.
  phase 1 스케줄러(`scheduler.py`)는 15분 경량 수집만 알고 있고, 22:00~06:00
  같은 시간창 정책을 아직 하드코딩하지 않았다 - 야간 스캔을 나중에 추가할 때
  기존 15분 수집과 어떻게 시간을 나눌지(REBUILD_CONCEPT.md 9절, 기존
  22:00~06:00 정책을 그대로 쓸지 새로 설계할지)는 아직 정하지 않은 채로 남겨
  뒀다. 지금 스케줄러는 그 결정을 선점하지 않는 선에서 최대한 단순하게
  만들었다.
- 알림 채널 다양화(command/webhook), 검색 인덱스, quota 연동, 다국어(KOR/ENG)
  UI, 정리 후보(cleanup candidates) 보고서, 최초 실행 마법사, 클릭 수 최소화
  단축 버튼 등 GUI 사용성 개선 후보들.
- 트레이 알림기(notifier) 프로세스 자체 - outbox JSON 파일 포맷은 갖춰
  뒀지만, 그 파일을 읽어 팝업을 띄우는 별도 프로세스는 아직 없다.

## 알려진 제약 / 로컬 개발 환경 메모

- 로컬 개발 PC(Windows, Python 3.10)에는 PyQt5와 `df`가 없다. 따라서 GUI와
  `collector.py`의 실제 `df` 호출 경로는 실제 VWP(RHEL, Python 3.12)에서
  검증이 필요하다. 비-GUI 로직(등급 계산, 설정 읽기/쓰기, df 출력 파싱,
  SQLite 저장, 알림 cooldown, 진단 판정)은 `tests/`에서 subprocess/파일시스템을
  모킹해 검증했다.
- `run.csh`는 csh 스크립트라 Windows에서 직접 실행/검증할 수 없었다 - 로직은
  기존 루트 `run.csh`의 검증된 패턴(진단 실패 시 exit 2, PYTHONHOME 경고 후
  제거 등)을 그대로 계승했다.
