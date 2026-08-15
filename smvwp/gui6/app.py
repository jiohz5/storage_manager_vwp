"""PyQt6 Fluent GUI 부트스트랩 (테마·DPI·데이터 경로).

`smvwp_cli.py gui6`가 호출한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

MIN_RECOMMENDED_FREE_BYTES = 500 * 1024 * 1024  # 500MB

FLUENT_MISSING_MESSAGE = """\
ERROR: PyQt6 Fluent GUI를 불러올 수 없습니다 ({error}).

이 화면은 외부 패키지가 필요합니다:

  pip install PyQt6 "PyQt6-Fluent-Widgets[full]" pyqtdarktheme

폐쇄망 VWP에서는 설치가 불가능하므로, 그 환경에서는 기본 GUI를 쓰세요:

  ./run.csh
"""


def _suggestion_text() -> str:
    """쓸 수 있는 후보와 여유 공간을 보여준다."""

    from .. import paths

    lines = []
    for info in paths.suggest_data_dirs():
        free = paths.format_bytes(info.free_bytes)
        state = "쓰기 가능" if info.usable else "쓰기 불가"
        lines.append(f"  {info.path}\n      여유 {free} · {state}")
    if not lines:
        return ""
    return "\n\n쓸 수 있을 만한 위치:\n" + "\n".join(lines)


def _prompt_for_data_dir(parent=None) -> Path:
    from PyQt6.QtWidgets import QFileDialog
    from qfluentwidgets import MessageBox

    from .. import paths

    box = MessageBox(
        "데이터 디렉터리 지정",
        "Storage Manager VWP를 처음 실행합니다.\n"
        "수집 데이터를 저장할, 모니터링 대상과는 분리된 쓰기 가능한 디렉터리를 선택하세요.\n"
        "모니터링 대상이 가득 차도 기록할 수 있도록 다른 파일시스템을 권장합니다."
        + _suggestion_text(),
        parent,
    )
    box.cancelButton.hide()
    box.exec()

    selected = QFileDialog.getExistingDirectory(parent, "데이터 디렉터리 선택")
    if not selected:
        raise SystemExit(2)

    # 고른 곳의 여유 공간을 바로 확인해 준다 - 나중에 가득 차서 수집이 멈추는
    # 것보다 지금 아는 편이 낫다.
    info = paths.describe_location(Path(selected))
    if info.free_bytes is not None and info.free_bytes < MIN_RECOMMENDED_FREE_BYTES:
        warn = MessageBox(
            "여유 공간 부족",
            f"선택한 위치의 여유 공간이 {paths.format_bytes(info.free_bytes)}뿐입니다.\n"
            f"권장 최소는 {paths.format_bytes(MIN_RECOMMENDED_FREE_BYTES)}입니다.\n"
            "공간이 부족하면 수집과 알림 기록이 멈출 수 있습니다.",
            parent,
        )
        warn.cancelButton.hide()
        warn.exec()
    return Path(selected)


def _resolve_data_dir(explicit) -> Path:
    from .. import paths

    resolved = paths.resolve_data_dir(explicit)
    if resolved is not None:
        return resolved
    resolved = _prompt_for_data_dir()
    paths.ensure_writable(resolved)
    paths.remember_data_dir(resolved)
    return resolved


def run(data_dir_arg=None) -> int:
    # High DPI에서 흐려지지 않도록 반올림 정책을 QApplication 생성 **전에**
    # 지정해야 한다.
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

        import qdarktheme
        from qfluentwidgets import setTheme, Theme
    except ImportError as exc:
        print(FLUENT_MISSING_MESSAGE.format(error=exc), file=sys.stderr)
        return 2

    app = QApplication(sys.argv)

    # Fluent 위젯은 setTheme이, Fluent가 아닌 기본 위젯(QFileDialog 등)은
    # qdarktheme이 담당한다. 둘 다 적용해야 이질감이 없다.
    setTheme(Theme.DARK)
    qdarktheme.setup_theme("dark", corner_shape="rounded")

    from .. import config as config_module
    from .. import paths
    from .main_window import MainWindow

    try:
        data_dir = _resolve_data_dir(data_dir_arg)
        config = config_module.load_config(data_dir)
    except (paths.DataDirError, config_module.ConfigError) as exc:
        from qfluentwidgets import MessageBox

        box = MessageBox("시작 실패", str(exc), None)
        box.cancelButton.hide()
        box.exec()
        return 1

    window = MainWindow(data_dir, config)
    window.show()
    return app.exec()
