"""PyQt6 + Fluent(Windows 11 스타일) GUI.

기존 `smvwp/gui`(PyQt5)를 **대체하지 않고 나란히** 둔다.

이유: 이 앱의 배포 대상은 폐쇄망 RHEL/VWP이고, 그곳에는 이미 설치된 PyQt5만
쓸 수 있다(`pip install` 불가, 관리자 권한 없음). `PyQt6-Fluent-Widgets`는
외부 패키지라 그 환경에 넣을 수 없으므로, 운영 배포본은 계속 `smvwp/gui`를
쓰고 이 패키지는 패키지 반입이 가능한 환경에서만 쓴다.

백엔드(`smvwp/*.py` 중 GUI가 아닌 모듈)는 애초에 Qt에 의존하지 않게 분리해
두었기 때문에, 여기서는 화면 계층만 새로 쓰면 된다.
"""
