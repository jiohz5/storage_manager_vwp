"""트레이 알림기 - outbox를 지켜보다 팝업을 띄우는 독립 프로세스.

메인 GUI와 **완전히 별개로** 동작한다. 관리 창을 닫아도(또는 아예 켜지 않아도)
cron이 쌓은 알림은 이 프로세스가 표시한다 - DESIGN.md 1부 5절이 요구한 구조다.

동작:
- 주기적으로 `outbox/`를 확인하고 아직 확인하지 않은 알림을 트레이 메시지로
  띄운다.
- 사용자가 트레이 메뉴에서 "확인"을 눌렀을 때만 읽음 처리한다. 팝업이 떴다는
  이유만으로 읽음 처리하지 않는다 (자리를 비운 사이 놓치지 않도록).
- 로그아웃 중 쌓인 알림은 다음 실행 때 "미확인 N건"으로 한 번에 요약한다.

`--install-autostart`는 XDG autostart 규격(`~/.config/autostart/*.desktop`)에
파일 하나를 쓴다. MATE·GNOME·KDE 모두 이 규격을 따르므로 데스크톱 환경별로
분기하지 않는다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from . import config as config_module
from . import i18n, paths, popup_queue, tiers

AUTOSTART_FILENAME = "storage-manager-vwp-notifier.desktop"
DEFAULT_POLL_SECONDS = 60


def autostart_dir() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "autostart"


def autostart_file() -> Path:
    return autostart_dir() / AUTOSTART_FILENAME


def build_autostart_entry(python_bin: str, script_dir: Path, data_dir: Path) -> str:
    """XDG autostart 항목 내용. 절대경로로 적는다 - 로그인 시점에는 작업
    디렉터리나 PATH를 신뢰할 수 없기 때문.

    경로는 `as_posix()`로 적는다. 이 항목은 RHEL/MATE 같은 POSIX 데스크톱에서만
    쓰이므로 항상 슬래시 표기가 맞고, 개발 PC(Windows)에서 만들어 봐도 같은
    결과가 나와 검증이 쉬워진다."""

    script = (script_dir / "smvwp_cli.py").as_posix()
    command = f'"{python_bin}" "{script}" notify --data-dir "{data_dir.as_posix()}"'
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Storage Manager VWP Notifier\n"
        "Comment=Storage capacity alert tray notifier\n"
        f"Exec={command}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def install_autostart(data_dir: Path, python_bin: Optional[str] = None) -> Path:
    python_bin = python_bin or sys.executable
    script_dir = Path(__file__).resolve().parent.parent
    target = autostart_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        build_autostart_entry(python_bin, script_dir, data_dir), encoding="utf-8", newline="\n"
    )
    return target


def remove_autostart() -> bool:
    target = autostart_file()
    if target.exists():
        target.unlink()
        return True
    return False


def is_autostart_installed() -> bool:
    return autostart_file().exists()


# -- 트레이 UI (Qt는 여기서만 import) -------------------------------------

def run_tray(data_dir: Path, config: config_module.AppConfig, poll_seconds: int = DEFAULT_POLL_SECONDS) -> int:
    """트레이 아이콘을 띄우고 outbox를 폴링한다. PyQt6가 필요하다."""

    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QAction, QColor, QIcon, QPixmap
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("ERROR: 시스템 트레이를 사용할 수 없습니다.", file=sys.stderr)
        return 2

    def _icon_for(tier: str) -> QIcon:
        # 외부 아이콘 파일에 의존하지 않고 등급 색상으로 단색 아이콘을 만든다
        # (폐쇄망 배포본을 가볍게 유지하고, 아이콘 테마에도 의존하지 않는다).
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(tiers.color(tier)))
        return QIcon(pixmap)

    tray = QSystemTrayIcon(_icon_for(tiers.NORMAL))
    menu = QMenu()
    status_action = QAction("", menu)
    status_action.setEnabled(False)
    menu.addAction(status_action)
    menu.addSeparator()
    acknowledge_action = QAction(i18n.t("common.yes"), menu)
    menu.addAction(acknowledge_action)
    quit_action = QAction(i18n.t("common.close"), menu)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.show()

    shown_event_ids = set()

    def refresh() -> None:
        pending = popup_queue.list_pending(data_dir)
        count = len(pending)
        status_action.setText(f"{i18n.t('app.title')} - {count}")
        worst = tiers.NORMAL
        for item in pending:
            if tiers.severity(item.tier) > tiers.severity(worst):
                worst = item.tier
        tray.setIcon(_icon_for(worst if count else tiers.NORMAL))
        tray.setToolTip(f"{i18n.t('app.title')}: {count}")

        new_items = [item for item in pending if item.event_id not in shown_event_ids]
        if not new_items:
            return
        shown_event_ids.update(item.event_id for item in new_items)
        if len(new_items) == 1:
            item = new_items[0]
            tray.showMessage(f"[{item.tier_label}] {item.account_name}", item.message)
        else:
            # 로그아웃 중 쌓인 것들은 하나씩 띄우지 않고 한 번에 요약한다.
            summary = "\n".join(f"- {item.message}" for item in new_items[:5])
            more = len(new_items) - 5
            if more > 0:
                summary += f"\n... (+{more})"
            tray.showMessage(f"{i18n.t('app.title')} ({len(new_items)})", summary)

    def acknowledge() -> None:
        # 사용자가 실제로 확인했을 때만 읽음 처리한다.
        popup_queue.mark_all_read(data_dir)
        refresh()

    acknowledge_action.triggered.connect(acknowledge)
    quit_action.triggered.connect(app.quit)
    tray.activated.connect(lambda reason: acknowledge() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(max(5, poll_seconds) * 1000)
    refresh()

    return app.exec()


def main_with_args(args) -> int:
    """이미 파싱된 인자로 실행한다 (`smvwp_cli.py notify`가 호출).

    인자 파싱을 단일 진입점으로 옮기고, 여기서는 동작만 담당한다."""

    data_dir = paths.resolve_data_dir(getattr(args, "data_dir", None))
    if data_dir is None:
        print(
            "ERROR: 데이터 디렉터리를 찾을 수 없습니다. --data-dir을 지정하거나 "
            "STORAGE_MANAGER_DATA_DIR을 설정하세요.",
            file=sys.stderr,
        )
        return 2

    if args.remove_autostart:
        print("자동 시작을 해제했습니다." if remove_autostart() else "등록된 자동 시작이 없습니다.")
        return 0

    if args.install_autostart:
        target = install_autostart(data_dir)
        print(f"자동 시작을 등록했습니다: {target}")
        return 0

    try:
        config = config_module.load_config(data_dir)
    except config_module.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    i18n.set_language(config.settings.language)

    if args.status:
        print(f"미확인 알림 {popup_queue.unread_count(data_dir)}건")
        print(f"자동 시작 등록: {'예' if is_autostart_installed() else '아니오'}")
        return 0

    return run_tray(
        data_dir, config, poll_seconds=getattr(args, "poll_seconds", DEFAULT_POLL_SECONDS)
    )
