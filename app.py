#!/usr/bin/env python3
"""Storage Manager VWP (phase 1) GUI 진입점.

`run.csh`가 사전 점검 후 이 스크립트를 실행한다. 데이터 디렉터리를 아직
모르면(포인터 파일도 없고 --data-dir/환경변수도 없으면) 사용자에게 한 번
물어보고, 이후에는 홈 디렉터리의 포인터 파일에 기억해 둔다
(REBUILD_CONCEPT.md 3절 "최초 실행 시 한 번 GUI에서 지정 ... 이후에는 작은
포인터 파일로 기억").
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# storage_manager_v2/ 를 sys.path에 명시적으로 추가해, run.csh가 어느
# 작업 디렉터리에서 호출되더라도 `smvwp` 패키지를 찾을 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smvwp import config as config_module  # noqa: E402
from smvwp import paths  # noqa: E402

# 수집 이력·보고서·알림을 담기에 최소한 이 정도는 있어야 한다는 기준. 여유가
# 이보다 적으면 경고한다 (막지는 않는다 - 폐쇄망에서 선택지가 적을 수 있다).
MIN_RECOMMENDED_FREE_BYTES = 500 * 1024 * 1024  # 500MB


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

    app = QApplication.instance() or QApplication(sys.argv)
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


def _resolve_data_dir(explicit: str | None) -> Path:
    resolved = paths.resolve_data_dir(explicit)
    if resolved is not None:
        return resolved
    resolved = _prompt_for_data_dir_gui()
    paths.ensure_writable(resolved)
    paths.remember_data_dir(resolved)
    return resolved


PYQT5_MISSING_MESSAGE = """\
ERROR: PyQt5를 불러올 수 없습니다 ({error}).

이 앱의 GUI는 PyQt5가 필요하지만, 폐쇄망이라 앱이 대신 설치해 줄 수 없습니다.
선택한 Python에 PyQt5가 들어 있는지 확인하세요:

  {python} -c "from PyQt5 import QtWidgets"

PyQt5가 있는 다른 Python 설치를 쓰신다면 STORAGE_MANAGER_PYTHON_BIN을 그쪽
실행 파일로 다시 지정하면 됩니다.

전체 진단은 다음으로 볼 수 있습니다 (GUI 없이 동작합니다):

  ./run.csh --diagnose

GUI 없이 수집만 하려면 아래 스크립트는 PyQt5 없이도 동작합니다:

  collector_cli.py   (15분 수집)
  nightly_scan_cli.py (야간 상세 스캔)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Storage Manager VWP")
    parser.add_argument("--data-dir", help="데이터 디렉터리 (미지정 시 저장된 위치 사용, 없으면 GUI에서 지정)")
    args = parser.parse_args()

    # PyQt5가 없을 때 파이썬 스택트레이스만 뜨면 폐쇄망에서 원인을 짚기 어렵다.
    # run.csh의 사전 점검은 의도적으로 --python-only(빠른 점검)라 PyQt5를 보지
    # 않으므로, GUI가 실제로 필요해지는 이 지점에서 친절히 안내한다.
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        print(
            PYQT5_MISSING_MESSAGE.format(error=exc, python=sys.executable),
            file=sys.stderr,
        )
        return 2

    app = QApplication(sys.argv)

    try:
        data_dir = _resolve_data_dir(args.data_dir)
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


if __name__ == "__main__":
    raise SystemExit(main())
