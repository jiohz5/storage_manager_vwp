"""PyQt6 + Fluent(Windows 11 스타일) GUI.

백엔드(`smvwp/*.py` 중 GUI가 아닌 모듈)는 Qt에 의존하지 않게 분리해 두었다.
덕분에 화면 계층을 통째로 갈아끼울 때 백엔드는 한 줄도 건드리지 않았고,
cron이 부르는 `collect`/`scan`은 지금도 Qt 없이 돈다.
"""
