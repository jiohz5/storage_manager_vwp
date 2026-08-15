"""MSFluentWindow 기반 메인 창.

좌측 네비게이션에 기능별 서브 인터페이스를 올린다 - 보고서·검색·설정이
각각 독립 화면이다.

언어 전환은 여전히 즉시 반영된다 (재시작 불필요). 각 인터페이스가
`retranslate()`를 제공하고, 여기서 한 번에 호출한다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from qfluentwidgets import (
    MSFluentWindow,
    NavigationItemPosition,
    setTheme,
    Theme,
)
from qfluentwidgets import FluentIcon as FIF

from .. import config as config_module
from .. import i18n
from .dashboard_interface import DashboardInterface
from .reports_interface import ReportsInterface
from .search_interface import SearchInterface
from .settings_interface import SettingsInterface


class MainWindow(MSFluentWindow):
    def __init__(self, data_dir: Path, config: config_module.AppConfig):
        super().__init__()
        self._data_dir = data_dir
        self._config = config
        i18n.set_language(config.settings.language)

        self.dashboard = DashboardInterface(data_dir, config, self)
        self.reports = ReportsInterface(data_dir, config, self)
        self.search = SearchInterface(data_dir, config, self)
        self.settings = SettingsInterface(data_dir, config, self)
        self.settings.set_saved_callback(self._on_settings_saved)

        self._init_navigation()
        self._init_window()
        self.retranslate()

    def _init_navigation(self) -> None:
        self.addSubInterface(self.dashboard, FIF.PIE_SINGLE, "")
        self.addSubInterface(self.reports, FIF.DOCUMENT, "")
        self.addSubInterface(self.search, FIF.SEARCH, "")
        self.addSubInterface(
            self.settings,
            FIF.SETTING,
            "",
            position=NavigationItemPosition.BOTTOM,
        )
        # 언어 전환은 자주 쓰지 않으므로 하단에 둔다.
        self.navigationInterface.addItem(
            routeKey="language",
            icon=FIF.LANGUAGE,
            text="",
            onClick=self._toggle_language,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )

    def _init_window(self) -> None:
        self.resize(1180, 760)
        self.setMinimumSize(960, 640)

    # -- 다국어 ------------------------------------------------------
    def retranslate(self) -> None:
        self.setWindowTitle(i18n.t("app.title"))
        # 네비게이션 항목 라벨
        labels = {
            self.dashboard.objectName(): i18n.t("app.title"),
            self.reports.objectName(): i18n.t("reports.title"),
            self.search.objectName(): i18n.t("search.title"),
            self.settings.objectName(): i18n.t("accounts.title"),
        }
        for route_key, text in labels.items():
            widget = self.navigationInterface.widget(route_key)
            if widget is not None:
                widget.setText(text)
        language_item = self.navigationInterface.widget("language")
        if language_item is not None:
            language_item.setText(i18n.t("menu.language"))

        for interface in (self.dashboard, self.reports, self.search, self.settings):
            interface.retranslate()

    def _toggle_language(self) -> None:
        """지원 언어를 순환한다. 항목이 둘뿐이라 토글로 충분하다."""

        languages = i18n.available_languages()
        current = i18n.get_language()
        nxt = languages[(languages.index(current) + 1) % len(languages)]

        i18n.set_language(nxt)
        self._config.settings.language = nxt
        try:
            config_module.save_config(self._data_dir, self._config)
        except config_module.ConfigError:
            # 저장에 실패해도 이번 세션의 화면 전환은 그대로 진행한다 (표시
            # 설정일 뿐이라 데이터 무결성과 무관).
            pass
        self.retranslate()
        self.dashboard._render_table(self.dashboard._latest_samples)
        self.dashboard._refresh_scan_section()

    def _on_settings_saved(self) -> None:
        """설정 화면에서 저장하면 나머지 화면을 새 설정으로 갱신한다."""

        self._config = config_module.load_config(self._data_dir)
        i18n.set_language(self._config.settings.language)
        self.dashboard.reload_config(self._config)
        self.reports.reload_config(self._config)
        self.search.reload_config(self._config)
        self.retranslate()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 콜백 이름 규칙)
        self.dashboard.shutdown()
        self.search.shutdown()
        super().closeEvent(event)
