"""최소 진단: Python 버전, 필요 표준 모듈, GUI 툴킷, 데이터 디렉터리 쓰기 권한.

기존 구현의 `run.csh --diagnose` / `runtime_check.py` / `verify_environment.py`
역할을 개념만 이어받아 훨씬 단순하게 하나로 합쳤다 (DESIGN.md 2부 3절
"진단과 실행이 한 번의 호출로 끝나는 느낌"). run.csh는 이 모듈을
`python -m smvwp.diagnostics`로 호출해 사전 점검 후 바로 앱을 띄운다.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import paths

REQUIRED_PYTHON = (3, 12)

# phase 1이 실제로 쓰는 표준 라이브러리만 나열한다 (필요 이상으로 점검을
# 늘리지 않는다).
REQUIRED_MODULES: Sequence[str] = (
    "json",
    "sqlite3",
    "subprocess",
    "dataclasses",
    "uuid",
    "argparse",
    "threading",
)


def check_python_version(version_info=None) -> dict:
    version_info = version_info or sys.version_info
    current = (version_info[0], version_info[1])
    ok = current >= REQUIRED_PYTHON
    return {
        "ok": ok,
        "current": f"{version_info[0]}.{version_info[1]}.{version_info[2]}",
        "required": f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+",
    }


def check_modules(module_names: Sequence[str] = REQUIRED_MODULES) -> dict:
    result = {}
    for name in module_names:
        try:
            importlib.import_module(name)
            result[name] = {"ok": True, "error": None}
        except ImportError as exc:
            result[name] = {"ok": False, "error": str(exc)}
    return result


def check_gui_toolkit() -> dict:
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR  # type: ignore
        import qfluentwidgets  # type: ignore  # noqa: F401
    except ImportError as exc:
        return {"available": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - 방어적 처리
        return {"available": False, "error": f"GUI 툴킷 import 중 오류: {exc}"}
    return {"available": True, "pyqt_version": PYQT_VERSION_STR, "qt_version": QT_VERSION_STR}


# Qt 6.5부터 새로 필수가 된 라이브러리. Qt5에는 없던 요구사항이라, PyQt5로는
# 잘 뜨던 장비에서 PyQt6로 바꾸면 정확히 이것 때문에 막히는 경우가 많다.
QT6_NEW_DEPENDENCIES = ("libxcb-cursor.so.0",)


def check_display_platform() -> dict:
    """플랫폼 플러그인이 실제로 로드되는지 확인한다.

    import만 성공하고 창은 못 뜨는 경우가 있다. 대표적으로 리눅스에서
    `Could not load the Qt platform plugin "xcb" ... even though it was found`
    - 플러그인 파일은 있는데 그것이 의존하는 시스템 .so가 없다는 뜻이다.

    이걸 실행 시점이 아니라 `--diagnose`에서 잡아야 한다. 반입된 장비에서
    창이 안 뜨는 이유를 짐작으로 찾게 두면 안 된다.

    실제로 `QGuiApplication`을 만들어 보되, 별도 프로세스에서 시도한다 -
    플러그인 로드 실패는 파이썬 예외가 아니라 abort로 끝나는 경우가 있어
    같은 프로세스에서 하면 진단 자체가 죽는다.
    """

    if sys.platform == "win32" or sys.platform == "darwin":
        # 플랫폼 플러그인 문제는 사실상 리눅스/X11 이야기다.
        return {"checked": False, "ok": True, "reason": "이 OS에서는 점검 생략"}

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return {
            "checked": True,
            "ok": False,
            "reason": "DISPLAY가 비어 있습니다 (원격 세션 안에서 실행해야 합니다)",
            "missing": [],
        }

    probe = (
        "import sys;"
        "from PyQt6.QtGui import QGuiApplication;"
        "app = QGuiApplication(['probe']);"
        "sys.exit(0)"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        return {"checked": True, "ok": False, "reason": str(exc), "missing": []}

    if completed.returncode == 0:
        return {"checked": True, "ok": True, "reason": None, "missing": []}

    stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
    missing = _missing_platform_libraries()
    return {
        "checked": True,
        "ok": False,
        "reason": stderr.splitlines()[0] if stderr else f"exit={completed.returncode}",
        "missing": missing,
    }


def _missing_platform_libraries() -> list:
    """xcb 플러그인이 못 찾는 공유 라이브러리 목록 (`ldd` 사용).

    `ldd`가 없거나 플러그인 경로를 못 찾으면 빈 목록을 돌려준다 - 추측한
    이름을 지어내지 않는다."""

    try:
        import PyQt6  # type: ignore
    except ImportError:
        return []

    plugin = (
        Path(PyQt6.__file__).parent / "Qt6" / "plugins" / "platforms" / "libqxcb.so"
    )
    if not plugin.exists():
        return []
    try:
        completed = subprocess.run(
            ["ldd", str(plugin)], capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    output = (completed.stdout or b"").decode("utf-8", errors="replace")
    return [
        line.split("=>")[0].strip()
        for line in output.splitlines()
        if "not found" in line
    ]


def check_data_dir(data_dir: Optional[Path]) -> dict:
    if data_dir is None:
        # 미지정은 실패가 아니다. GUI가 최초 실행 때 한 번 물어보도록 설계했고,
        # 설치 중 --diagnose를 돌리는 시점에는 아직 안 정한 것이 정상이다.
        # 여기서 FAIL을 내면 정상 설치 중인 사용자가 뭔가 잘못된 줄 안다.
        # (cron은 이야기가 다르지만, 그건 setup_cron.csh가 따로 막는다.)
        return {"configured": False, "ok": True, "path": None, "error": None}
    try:
        paths.ensure_writable(data_dir)
    except paths.DataDirError as exc:
        return {"configured": True, "ok": False, "path": str(data_dir), "error": str(exc)}
    return {"configured": True, "ok": True, "path": str(data_dir), "error": None}


def run_diagnostics(
    data_dir: Optional[Path] = None,
    version_info=None,
    include_pyqt5: bool = True,
) -> dict:
    python_result = check_python_version(version_info)
    modules_result = check_modules()
    pyqt5_result = check_gui_toolkit() if include_pyqt5 else {"available": None, "skipped": True}
    # 툴킷을 불러올 수 있을 때만 화면 점검이 의미가 있다.
    display_result = (
        check_display_platform()
        if pyqt5_result.get("available")
        else {"checked": False, "ok": True, "reason": None, "missing": []}
    )
    data_dir_result = check_data_dir(data_dir)

    modules_ok = all(item["ok"] for item in modules_result.values())
    # GUI 툴킷 실패는 화면을 못 띄운다는 뜻이지만, --diagnose 자체는 정보 제공이
    # 목적이므로 overall ok 판정에는 Python 버전/모듈/데이터 디렉터리만 반영하고
    # 툴킷은 별도 경고로 취급한다 (수집 전용 CLI는 GUI 없이도 돌아야 함).
    overall_ok = python_result["ok"] and modules_ok and data_dir_result["ok"]

    return {
        "ok": overall_ok,
        "python": python_result,
        "modules": modules_result,
        "pyqt5": pyqt5_result,
        "display": display_result,
        "data_dir": data_dir_result,
    }


def format_report(result: dict) -> str:
    lines = []
    python = result["python"]
    lines.append(
        f"Python: {python['current']} (필요: {python['required']}) - "
        f"{'OK' if python['ok'] else 'FAIL'}"
    )
    for name, info in result["modules"].items():
        status = "OK" if info["ok"] else f"FAIL ({info['error']})"
        lines.append(f"모듈 {name}: {status}")
    pyqt5 = result["pyqt5"]
    if pyqt5.get("skipped"):
        lines.append("GUI 툴킷: 점검 생략")
    elif pyqt5.get("available"):
        lines.append(f"GUI 툴킷: PyQt {pyqt5['pyqt_version']} / Qt {pyqt5['qt_version']} - OK")
    else:
        lines.append(f"GUI 툴킷: 사용 불가 ({pyqt5.get('error')})")
    display = result.get("display") or {}
    if display.get("checked"):
        if display.get("ok"):
            lines.append("화면 플러그인: OK")
        else:
            lines.append(f"화면 플러그인: 사용 불가 ({display.get('reason')})")
            for name in display.get("missing") or []:
                lines.append(f"    없는 라이브러리: {name}")
            for hint in _display_hints(display):
                lines.append(f"    {hint}")

    data_dir = result["data_dir"]
    if not data_dir["configured"]:
        lines.append("데이터 디렉터리: 미지정 (정상 - 최초 실행 시 GUI에서 지정합니다)")
    elif data_dir["ok"]:
        lines.append(f"데이터 디렉터리: {data_dir['path']} - 쓰기 OK")
    else:
        lines.append(f"데이터 디렉터리: {data_dir['path']} - 오류: {data_dir['error']}")

    lines.append(f"종합 결과: {'OK' if result['ok'] else 'FAIL'}")

    # GUI 툴킷은 종합 판정에 넣지 않는다 (수집 전용 CLI는 Qt 없이도 동작해야
    # 하므로). 다만 그대로 두면 "사용 불가 + 종합 OK"가 나란히 찍혀 모순처럼
    # 보이므로, 무엇이 되고 무엇이 안 되는지 한 줄로 못박는다.
    if not pyqt5.get("skipped") and not pyqt5.get("available"):
        lines.append(
            "  └ 단, GUI 툴킷이 없어 화면은 띄울 수 없습니다. "
            "수집 전용 CLI(smvwp_cli.py collect / scan)는 사용 가능합니다."
        )
    return "\n".join(lines)


def _display_hints(display: dict) -> list:
    """무엇을 하면 되는지 알려준다. 원인별로 다른 조치가 필요하다."""

    reason = (display.get("reason") or "")
    if "DISPLAY" in reason:
        return ["원격 데스크톱(DCV 등) 세션 안에서 실행하세요."]

    missing = display.get("missing") or []
    hints = []
    if any(name.startswith("libxcb-cursor") for name in missing) or not missing:
        # Qt5에는 없던 요구사항이라 PyQt6로 옮기면 여기서 처음 막힌다.
        hints.append(
            "Qt 6.5부터 libxcb-cursor가 필요합니다 (Qt5에는 없던 요구사항)."
        )
        hints.append("관리자: yum install xcb-util-cursor libxkbcommon-x11")
        hints.append(
            "비관리자: RPM만 받아 홈에 풀고 LD_LIBRARY_PATH에 추가해도 됩니다."
        )
    hints.append("자세한 원인: QT_DEBUG_PLUGINS=1 로 다시 실행")
    return hints


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Storage Manager VWP 진단")
    parser.add_argument("--data-dir")
    parser.add_argument("--python-only", action="store_true", help="Python 버전/모듈만 점검하고 종료")
    args = parser.parse_args()

    if args.python_only:
        python_result = check_python_version()
        modules_result = check_modules()
        ok = python_result["ok"] and all(item["ok"] for item in modules_result.values())
        print(format_report({
            "ok": ok,
            "python": python_result,
            "modules": modules_result,
            "pyqt5": {"skipped": True},
            "display": {"checked": False, "ok": True, "reason": None, "missing": []},
            "data_dir": {"configured": False, "ok": False, "path": None, "error": None},
        }))
        return 0 if ok else 1

    resolved = paths.resolve_data_dir(args.data_dir)
    result = run_diagnostics(resolved)
    print(format_report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
