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


def _prompt_for_data_dir_gui() -> Path:
    from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.information(
        None,
        "데이터 디렉터리 지정",
        "Storage Manager VWP를 처음 실행합니다.\n"
        "수집 데이터를 저장할, 모니터링 대상과는 분리된 쓰기 가능한 디렉터리를 선택하세요.",
    )
    selected = QFileDialog.getExistingDirectory(None, "데이터 디렉터리 선택")
    if not selected:
        QMessageBox.critical(None, "데이터 디렉터리 필요", "데이터 디렉터리를 선택하지 않아 종료합니다.")
        raise SystemExit(2)
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
