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


def main() -> int:
    parser = argparse.ArgumentParser(description="Storage Manager VWP")
    parser.add_argument("--data-dir", help="데이터 디렉터리 (미지정 시 저장된 위치 사용, 없으면 GUI에서 지정)")
    args = parser.parse_args()

    from PyQt5.QtWidgets import QApplication, QMessageBox

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
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
