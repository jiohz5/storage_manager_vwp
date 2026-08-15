#!/usr/bin/env python3
"""Storage Manager VWP 단일 진입점.

    ./smvwp_cli.py gui                     # 관리 GUI (run.csh가 호출)
    ./smvwp_cli.py collect                 # 15분 수집 한 번 (cron)
    ./smvwp_cli.py scan                    # 야간 상세 스캔 (cron, 22:00~06:00)
    ./smvwp_cli.py scan --now              # 시간창 무시하고 지금 실행
    ./smvwp_cli.py scan --stop             # 실행 중인 스캔에 안전 중지 요청
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

NO_GUI_TOOLKIT_MESSAGE = """\
ERROR: GUI 툴킷을 하나도 불러올 수 없습니다.

  PyQt6 Fluent: {fluent_error}
  PyQt5       : {classic_error}

둘 중 하나는 있어야 화면을 띄울 수 있습니다. 사내 프록시로 설치할 수 있다면
Fluent 쪽을 권합니다:

  {python} -m pip install PyQt6 "PyQt6-Fluent-Widgets[full]" pyqtdarktheme

PyQt6가 DLL 오류를 내면 검증된 조합으로 내려 쓰세요:

  {python} -m pip install "PyQt6==6.7.1" "PyQt6-Qt6==6.7.3" "PyQt6-sip==13.8.0"

이미 설치된 다른 Python을 쓰시려면 STORAGE_MANAGER_PYTHON_BIN을 그쪽 실행
파일로 다시 지정하면 됩니다.

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
    """GUI를 띄운다. **Fluent(PyQt6)를 먼저 시도하고, 없으면 PyQt5로 넘어간다.**

    사내 프록시로 패키지를 받을 수 있는 곳에서는 Fluent가 뜨고, 그렇지 못한
    장비에서는 이미 설치된 PyQt5가 뜬다. 같은 배포본을 두 환경에 그대로 반입할
    수 있게 하려는 것이다 - 장비마다 실행 명령을 다르게 안내하면 반드시 누군가
    틀린 쪽을 쓴다.

    `--classic`으로 PyQt5를 강제할 수 있다 (Fluent 쪽 문제를 갈라낼 때 유용).
    """

    fluent_error = None
    if not getattr(args, "classic", False):
        try:
            from smvwp.gui6.app import run as run_fluent
        except ImportError as exc:  # Fluent 스택이 없는 장비
            fluent_error = exc
        else:
            return run_fluent(args.data_dir)

    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
    except ImportError as classic_error:
        # 둘 다 없으면 파이썬 스택트레이스 대신 무엇을 깔면 되는지 알려준다.
        print(
            NO_GUI_TOOLKIT_MESSAGE.format(
                fluent_error=fluent_error or "건너뜀(--classic)",
                classic_error=classic_error,
                python=sys.executable,
            ),
            file=sys.stderr,
        )
        return 2

    app = QApplication(sys.argv)
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


def command_gui6(args) -> int:
    """Fluent GUI를 **강제**로 띄운다 (폴백 없음).

    `gui`가 이미 Fluent를 우선하므로 평소에는 필요 없다. Fluent가 왜 안 뜨는지
    확인할 때 쓴다 - 폴백이 없으니 원인이 그대로 드러난다."""

    from smvwp.gui6.app import run

    return run(args.data_dir)


# -- collect ---------------------------------------------------------------


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

    summary = nightly_scan.run_nightly_scan(
        data_dir,
        config,
        triggered_by="terminal" if args.now else "cron",
        bypass_window=args.now,
    )

    if not summary.started:
        print(f"실행하지 않음: {summary.reason}")
        return 0

    print(f"야간 상세 스캔 종료 (run_id={summary.run_id}, 상태={summary.status})")
    for outcome in summary.accounts:
        print(
            f"  - {outcome.account_name}: 기준선 세대 {outcome.baseline_generation} "
            f"({outcome.baseline_status}), 활동 스캔 pass {outcome.activity_pass} "
            f"({outcome.activity_status})"
        )
    return 0 if summary.status in (nightly_scan.STATUS_COMPLETED, nightly_scan.STATUS_PAUSED) else 1


# -- notify ----------------------------------------------------------------

def command_notify(args) -> int:
    from smvwp import notifier

    return notifier.main_with_args(args)


# -- 진입점 -----------------------------------------------------------------

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

    gui = sub.add_parser("gui", help="관리 GUI 실행 (Fluent 우선, 없으면 PyQt5)")
    _add_data_dir(gui)
    gui.add_argument(
        "--classic",
        action="store_true",
        help="Fluent를 건너뛰고 PyQt5 화면을 강제로 사용",
    )
    gui.set_defaults(func=command_gui)

    gui6 = sub.add_parser("gui6", help="Fluent GUI 강제 실행 (폴백 없음, 문제 확인용)")
    _add_data_dir(gui6)
    gui6.set_defaults(func=command_gui6)

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
    scan.set_defaults(func=command_scan)

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
