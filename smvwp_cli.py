#!/usr/bin/env python3
"""Storage Manager VWP 단일 진입점.

    ./smvwp_cli.py gui                     # 관리 GUI (run.csh가 호출)
    ./smvwp_cli.py collect                 # 15분 수집 한 번 (cron)
    ./smvwp_cli.py scan                    # 야간 상세 스캔 (cron, 22:00~06:00)
    ./smvwp_cli.py scan --now              # 시간창 무시하고 지금 실행
    ./smvwp_cli.py scan --stop             # 실행 중인 스캔에 안전 중지 요청
    ./smvwp_cli.py scan --now --parallel 4 # 볼륨 4개 동시 - 부하 실측용
    ./smvwp_cli.py scan --now --engine du # 예전 du 방식으로 한 번만
    ./smvwp_cli.py notify                  # 트레이 알림기 실행
    ./smvwp_cli.py notify --install-autostart

이전에는 하위 명령마다 별도 스크립트(app.py, collector_cli.py,
nightly_scan_cli.py, notifier_cli.py)를 뒀는데, 넷 다 "sys.path를 잡고 모듈을
호출한다"는 같은 껍데기였다. 폐쇄망에는 파일 하나하나가 반입 대상이므로 굳이
넷으로 나눠 둘 이유가 없어 하나로 합쳤다.

**PyQt5는 GUI/트레이 경로에서만 import한다.** cron이 부르는 `collect`와
`scan`은 화면이 없는 환경에서 돌아야 하므로, 모듈 최상단에서 PyQt5를 건드리면
안 된다 (각 핸들러 안에서 늦게 import한다).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 어느 작업 디렉터리에서 호출되더라도 `smvwp` 패키지를 찾을 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smvwp import config as config_module  # noqa: E402
from smvwp import paths  # noqa: E402

# 수집 이력·보고서·알림을 담기에 최소한 이 정도는 있어야 한다는 기준. 여유가
# 이보다 적으면 경고한다 (막지는 않는다 - 폐쇄망에서 선택지가 적을 수 있다).
MIN_RECOMMENDED_FREE_BYTES = 500 * 1024 * 1024  # 500MB

PYQT5_MISSING_MESSAGE = """\
ERROR: PyQt5를 불러올 수 없습니다 ({error}).

지금 쓰고 있는 Python은 다음입니다:

  {python}

이 앱의 화면은 PyQt5만 씁니다 (추가 테마 패키지 없이 자체 QSS를 입힙니다).
사내에 PyQt5가 들어 있는 Python이 따로 있다면 STORAGE_MANAGER_PYTHON_BIN을
그쪽 실행 파일로 다시 지정하세요.

전체 진단은 다음으로 볼 수 있습니다 (GUI 없이 동작합니다):

  ./run.csh --diagnose

GUI 없이 수집만 하려면 아래 하위 명령은 GUI 툴킷 없이도 동작합니다:

  ./smvwp_cli.py collect
  ./smvwp_cli.py scan
"""

DATA_DIR_MISSING_MESSAGE = (
    "ERROR: 데이터 디렉터리를 찾을 수 없습니다. --data-dir을 지정하거나 "
    "STORAGE_MANAGER_DATA_DIR을 설정하거나, GUI를 한 번 실행해 위치를 저장하세요."
)


def _resolve_or_fail(explicit):
    """헤드리스 경로용 데이터 디렉터리 해석.

    cron은 대화형이 아니므로 물어볼 수 없다. 못 찾으면 조용히 기본값을 만들지
    않고 명확히 실패한다 - 엉뚱한 곳에 수집 이력이 쌓이는 것보다 낫다."""

    data_dir = paths.resolve_data_dir(explicit)
    if data_dir is None:
        print(DATA_DIR_MISSING_MESSAGE, file=sys.stderr)
        raise SystemExit(2)
    return data_dir


# -- gui -------------------------------------------------------------------

def _suggestion_text() -> str:
    """쓸 수 있는 후보와 여유 공간을 보여준다.

    폐쇄망에서 관리자가 아니면 데이터를 둘 곳이 몇 군데 없다. 빈 파일 선택창만
    띄우면 어디를 골라야 할지 알 수 없으므로, 확실히 사용자 것인 위치와 그
    여유 공간을 먼저 보여주고 판단하게 한다."""

    lines = []
    for info in paths.suggest_data_dirs():
        free = paths.format_bytes(info.free_bytes)
        state = "쓰기 가능" if info.usable else "쓰기 불가"
        lines.append(f"  {info.path}\n      여유 {free} · {state}")
    if not lines:
        return ""
    return "\n\n쓸 수 있을 만한 위치:\n" + "\n".join(lines)


def _prompt_for_data_dir_gui() -> Path:
    from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

    QApplication.instance() or QApplication(sys.argv)
    QMessageBox.information(
        None,
        "데이터 디렉터리 지정",
        "Storage Manager VWP를 처음 실행합니다.\n"
        "수집 데이터를 저장할, 모니터링 대상과는 분리된 쓰기 가능한 디렉터리를 선택하세요.\n"
        "모니터링 대상이 가득 차도 기록할 수 있도록 다른 파일시스템을 권장합니다."
        + _suggestion_text(),
    )
    selected = QFileDialog.getExistingDirectory(None, "데이터 디렉터리 선택")
    if not selected:
        QMessageBox.critical(None, "데이터 디렉터리 필요", "데이터 디렉터리를 선택하지 않아 종료합니다.")
        raise SystemExit(2)

    # 고른 곳의 여유 공간을 바로 확인해 준다 - 나중에 가득 차서 수집이 멈추는
    # 것보다 지금 아는 편이 낫다.
    info = paths.describe_location(Path(selected))
    if info.free_bytes is not None and info.free_bytes < MIN_RECOMMENDED_FREE_BYTES:
        QMessageBox.warning(
            None,
            "여유 공간 부족",
            f"선택한 위치의 여유 공간이 {paths.format_bytes(info.free_bytes)}뿐입니다.\n"
            f"권장 최소는 {paths.format_bytes(MIN_RECOMMENDED_FREE_BYTES)}입니다.\n"
            "공간이 부족하면 수집과 알림 기록이 멈출 수 있습니다.",
        )
    return Path(selected)


def _resolve_data_dir_interactive(explicit) -> Path:
    resolved = paths.resolve_data_dir(explicit)
    if resolved is not None:
        return resolved
    resolved = _prompt_for_data_dir_gui()
    paths.ensure_writable(resolved)
    paths.remember_data_dir(resolved)
    return resolved


def command_gui(args) -> int:
    """관리 GUI를 띄운다.

    PyQt5가 없을 때 파이썬 스택트레이스만 뜨면 반입된 장비에서 원인을 짚기
    어렵다. GUI가 실제로 필요해지는 이 지점에서 친절히 안내한다."""

    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        print(
            PYQT5_MISSING_MESSAGE.format(error=exc, python=sys.executable),
            file=sys.stderr,
        )
        return 2

    app = QApplication(sys.argv)

    # 위젯을 만들기 전에 테마를 입힌다 - 나중에 적용하면 이미 만들어진
    # 위젯이 기본 외양으로 한 번 그려졌다가 바뀌어 깜빡인다.
    from smvwp.gui.theme import apply as apply_theme

    apply_theme(app)

    try:
        data_dir = _resolve_data_dir_interactive(args.data_dir)
        config = config_module.load_config(data_dir)
    except (paths.DataDirError, config_module.ConfigError) as exc:
        QMessageBox.critical(None, "시작 실패", str(exc))
        return 1

    from smvwp.gui.main_window import MainWindow

    window = MainWindow(data_dir, config)
    window.show()
    # 창이 뜬 뒤에 안내를 띄운다 - 부모 창 없이 모달이 먼저 뜨는 것을 피한다.
    window.show_first_run_if_needed()
    return app.exec_()


def command_collect(args) -> int:
    from smvwp.cycle import run_collection_cycle

    data_dir = _resolve_or_fail(args.data_dir)
    try:
        config = config_module.load_config(data_dir)
    except config_module.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    records = run_collection_cycle(data_dir, config)
    failed = [r for r in records if not r.ok]
    print(f"수집 완료: {len(records)}개 계정, 실패 {len(failed)}건")
    for record in failed:
        print(f"  - {record.account_id}: {record.error_message}")
    return 0


# -- scan ------------------------------------------------------------------

def command_scan(args) -> int:
    from smvwp import nightly_scan

    data_dir = _resolve_or_fail(args.data_dir)

    if args.stop:
        if nightly_scan.request_stop(data_dir):
            print("중지를 요청했습니다. 실행 중인 스캔이 다음 체크포인트에서 안전하게 멈춥니다.")
            return 0
        print("현재 실행 중인 야간 스캔이 없습니다.")
        return 1

    try:
        config = config_module.load_config(data_dir)
    except config_module.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.status:
        return _print_scan_status(data_dir, config)

    # 설정 파일을 고치지 않고 이번 실행만 다른 엔진으로 잰다. 두 방법을 같은
    # 밤에 번갈아 돌려 비교할 때 쓴다.
    if args.engine:
        config.settings.scan_engine = args.engine

    summary = nightly_scan.run_nightly_scan(
        data_dir,
        config,
        triggered_by="terminal" if args.now else "cron",
        bypass_window=args.now,
        parallel_accounts=args.parallel,
    )

    if not summary.started:
        print(f"실행하지 않음: {summary.reason}")
        return 0

    # 동시 계정 수가 왜 그 값인지를 같이 적는다 - 숫자만 보면 주말이라 3인
    # 것인지 설정이 잘못된 것인지 구분이 안 된다.
    night = "주말 밤" if summary.weekend_night else "평일 밤"
    print(
        f"야간 상세 스캔 종료 (run_id={summary.run_id}, 상태={summary.status}, "
        f"{night} · 동시 볼륨={summary.parallel_accounts} · "
        f"엔진={config.settings.scan_engine})"
    )
    for outcome in summary.accounts:
        print(
            f"  - {outcome.account_name}: 기준선 세대 {outcome.baseline_generation} "
            f"({outcome.baseline_status}), 활동 스캔 pass {outcome.activity_pass} "
            f"({outcome.activity_status})"
        )
    return 0 if summary.status in (nightly_scan.STATUS_COMPLETED, nightly_scan.STATUS_PAUSED) else 1


def _fmt_seconds(value) -> str:
    if value is None:
        return "-"
    if value < 60:
        return f"{value:.0f}초"
    if value < 3600:
        return f"{value / 60:.1f}분"
    return f"{value / 3600:.1f}시간"


def _print_scan_status(data_dir, config) -> int:
    """스캔을 돌리지 않고 "지금 어디까지 갔고 왜 더딘지"를 출력한다.

    화면과 보고서는 "얼마나 갔나"만 보여 준다. 밤새 돌았는데 한 계정만 갔을 때
    필요한 것은 **왜 안 갔나**이고, 그 답은 대개 두 숫자에 있다 - 분할로 생긴
    작업 비율과 체크포인트 하나에 걸린 시간.
    """

    from smvwp import scan_store

    conn = scan_store.connect(data_dir)
    try:
        run = scan_store.latest_run(conn)
        if run is None:
            print("아직 실행된 스캔이 없습니다.")
            return 0

        keys = run.keys()
        parallel = run["parallel_accounts"] if "parallel_accounts" in keys else None
        weekend = run["weekend_night"] if "weekend_night" in keys else None
        night = "-"
        if weekend is not None:
            night = "주말 밤" if weekend else "평일 밤"
        print(f"최근 실행  {run['run_id']}  상태={run['status']}")
        print(f"  시작 {str(run['started_at'])[:19]}  종료 {str(run['ended_at'] or '-')[:19]}")
        print(f"  {night} · 동시 볼륨 {parallel or 1}개")
        print()

        header = (
            f"{'계정':<20}{'완료':>7}{'대기':>7}{'분할':>7}{'실패':>7}"
            f"{'분할로생긴작업':>16}{'중앙시간':>11}{'최장':>11}"
        )
        print(header)
        print("-" * 92)

        for account in config.accounts:
            state = scan_store.get_account_state(conn, account.account_id)
            diag = scan_store.diagnose_account(
                conn, account.account_id, state.working_generation
            )
            if not diag.total:
                print(f"{account.name:<20}{'아직 시작 안 함':>7}")
                continue
            print(
                f"{account.name:<20}{diag.done:>7,}{diag.pending:>7,}"
                f"{diag.split:>7,}{diag.error:>7,}"
                f"{diag.from_split:>10,} ({diag.split_ratio * 100:>3.0f}%)"
                f"{_fmt_seconds(diag.median_seconds):>11}"
                f"{_fmt_seconds(diag.slowest_seconds):>11}"
            )
            if diag.last_path:
                print(f"{'':<20}마지막: {diag.last_path}")

        print()
        budget = config.settings.detail_task_timeout_seconds
        print(f"디렉터리 하나당 시간 예산: {_fmt_seconds(budget)}")
        print()
        print("읽는 법")
        print("  · '분할로 생긴 작업' 비율이 높다 = 시간 초과가 반복됐다는 뜻입니다.")
        print("    분할하면 그 서브트리를 **처음부터 다시** 걷습니다 - 여기가 높으면")
        print("    같은 파일을 여러 번 세고 있는 것이라, 시간 예산을 늘리는 편이")
        print("    오히려 빠릅니다 (du -k는 시간 초과가 나도 그때까지 출력한 것을")
        print("    저장하므로, 예산을 늘려도 잃는 것이 없습니다).")
        print("  · '중앙 시간'이 시간 예산에 가깝다 = 대부분이 예산을 다 쓰고 잘렸다는")
        print("    뜻입니다. 같은 결론입니다.")
        print("  · 평일 밤은 동시 계정이 1개입니다 - 한 계정이 밤을 다 쓰면 나머지는")
        print("    그날 스캔되지 않습니다 (다음 밤에 순서가 돌아갑니다).")
    finally:
        conn.close()
    return 0


# -- notify ----------------------------------------------------------------

def command_notify(args) -> int:
    from smvwp import notifier

    return notifier.main_with_args(args)


# -- 진입점 -----------------------------------------------------------------

def command_probe(args) -> int:
    """지정 경로를 여러 각도로 재고 **짧은 코드**로 요약한다.

    폐쇄망에서 사람이 화면을 보고 옮겨 적는다는 전제로 만들었다. 그래서 기본
    출력이 세 줄이고, 상세 표는 그 아래에 참고용으로만 붙는다.
    """

    from smvwp import probe

    seconds = args.seconds if args.seconds else probe.DEFAULT_SLICE_SECONDS
    target = Path(args.path).expanduser()
    if not target.is_dir():
        print(f"ERROR: 디렉터리가 아닙니다: {target}", file=sys.stderr)
        return 1

    accounts = []
    volume_key = None
    needs_peers = not args.no_peers and not (args.peer and args.peer2)
    if needs_peers:
        # 이웃 계정을 찾으려면 설정이 필요하다. 없어도 진단 자체는 돌아간다 -
        # 그 경우 L·M 이 0(재지 못함)으로 남을 뿐이다. 진단을 통째로 못 돌리는
        # 것보다 그 두 칸이 비는 편이 낫다.
        data_dir = paths.resolve_data_dir(args.data_dir)
        if data_dir is None:
            print("(데이터 디렉터리를 몰라 이웃 계정 비교는 건너뜁니다. "
                  "--peer/--peer2 로 직접 줄 수 있습니다)")
        else:
            try:
                accounts = config_module.load_config(data_dir).accounts
                volume_key = paths.volume_key
            except Exception as exc:  # 설정이 깨졌어도 진단은 계속한다
                print(f"(설정을 읽지 못해 이웃 계정 비교는 건너뜁니다: {exc})")

    say = (lambda message: None) if args.codes_only else print
    if not args.codes_only:
        sweeps = (
            probe.QUICK_THREAD_SWEEP + probe.QUICK_PROCESS_SWEEP if args.quick
            else probe.THREAD_SWEEP + probe.PROCESS_SWEEP
        )
        pieces = len(sweeps) + 1
        pairs = 3 * 5
        print(f"대상: {target}")
        print(
            f"예상 소요: 전체 순회 1회 + du 1회 + 조각 {pieces + pairs}개"
            f"(조각당 {seconds:.0f}초) ≈ 순회시간 + du시간 + "
            f"{int((pieces + pairs) * seconds) // 60}분"
        )
        print("이 명령은 아무것도 쓰지 않습니다 (읽기 전용).")
        print("-" * 60)

    result = probe.run_probe(
        str(target),
        accounts=accounts,
        volume_key=volume_key,
        slice_seconds=seconds,
        workers=args.workers,
        quick=args.quick,
        process_threads=args.process_threads,
        peer_same_filer=args.peer,
        peer_other_filer=args.peer2,
        log=say,
    )

    if not args.codes_only:
        print("-" * 60)
    print(probe.format_transfer(result))
    if not args.codes_only:
        print(probe.format_detail(result))
        print("")
        print("위의 CODE / CPU / VAL 줄만 옮겨 주시면 됩니다 (줄 이름도 함께).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smvwp_cli.py", description="Storage Manager VWP"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_data_dir(target):
        target.add_argument(
            "--data-dir",
            help="데이터 디렉터리 (미지정 시 STORAGE_MANAGER_DATA_DIR 또는 저장된 위치)",
        )

    gui = sub.add_parser("gui", help="관리 GUI 실행")
    _add_data_dir(gui)
    gui.set_defaults(func=command_gui)


    collect = sub.add_parser("collect", help="15분 경량 수집 1회 (cron용)")
    _add_data_dir(collect)
    collect.set_defaults(func=command_collect)

    scan = sub.add_parser("scan", help="야간 상세 스캔 (cron용)")
    _add_data_dir(scan)
    scan.add_argument(
        "--now",
        action="store_true",
        help="시간창(22:00~06:00)을 무시하고 지금 실행 - 터미널 직접 실행용 진단/복구 경로",
    )
    scan.add_argument("--stop", action="store_true", help="실행 중인 스캔에 안전 중지 요청")
    scan.add_argument(
        "--status",
        action="store_true",
        help="스캔을 돌리지 않고 진행 상황만 진단해서 출력 (왜 더딘지 포함)",
    )
    scan.add_argument(
        "--parallel",
        type=int,
        default=None,
        metavar="N",
        help=(
            "서로 다른 볼륨 N개를 동시에 스캔한다 (기본: 설정값, 보통 1=직렬). "
            "같은 볼륨의 계정은 이 값과 무관하게 하나씩 돈다 - 같은 볼륨을 "
            "여럿이 두들겨 봐야 빨라지지 않는다. 결과는 보고서의 "
            "'스캔 중 리소스 변화'에서 확인한다."
        ),
    )
    scan.add_argument(
        "--engine",
        choices=config_module.SCAN_ENGINES,
        default=None,
        help=(
            "이번 실행만 다른 측정 엔진을 쓴다 (기본: 설정값). "
            "python=파이썬 순회(빠름), du=du -k 실행(nice/ionice가 듣는다). "
            "설정 파일은 건드리지 않는다."
        ),
    )
    scan.set_defaults(func=command_scan)

    probe = sub.add_parser(
        "probe",
        help="지정 경로를 여러 각도로 재고 짧은 진단 코드로 요약 (읽기 전용)",
    )
    _add_data_dir(probe)
    probe.add_argument("--path", required=True, help="진단 대상 디렉터리")
    probe.add_argument(
        "--seconds", type=float, default=None,
        help="시간을 잘라 재는 조각 하나의 길이(초). 기본 15",
    )
    probe.add_argument(
        "--workers", type=int, default=4,
        help="순회 스레드 수 (기본 4 - 야간 스캔이 쓰는 값과 같게)",
    )
    probe.add_argument(
        "--quick", action="store_true",
        help="스레드/프로세스 확장을 x1/x4만 본다 (시간 절반)",
    )
    probe.add_argument(
        "--process-threads", type=int, default=2,
        help=(
            "프로세스 측정에서 프로세스마다 쓸 스레드 수 (기본 2). "
            "프로세스 수 × 이 값이 총 동시 요청 수가 된다."
        ),
    )
    probe.add_argument(
        "--peer", default=None,
        help="같은 파일러의 다른 볼륨 경로 (미지정 시 계정 목록에서 자동으로 찾음)",
    )
    probe.add_argument(
        "--peer2", default=None,
        help="다른 파일러의 경로 (미지정 시 계정 목록에서 자동으로 찾음)",
    )
    probe.add_argument(
        "--no-peers", action="store_true",
        help="이웃 계정 비교(L·M)를 아예 건너뛴다",
    )
    probe.add_argument(
        "--codes-only", action="store_true",
        help="진행 로그 없이 옮겨 적을 세 줄만 출력",
    )
    probe.set_defaults(func=command_probe)

    notify = sub.add_parser("notify", help="트레이 알림기")
    _add_data_dir(notify)
    notify.add_argument("--poll-seconds", type=int, default=60)
    notify.add_argument("--install-autostart", action="store_true", help="로그인 시 자동 시작 등록")
    notify.add_argument("--remove-autostart", action="store_true", help="로그인 자동 시작 해제")
    notify.add_argument("--status", action="store_true", help="미확인 알림 수만 출력하고 종료")
    notify.set_defaults(func=command_notify)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
