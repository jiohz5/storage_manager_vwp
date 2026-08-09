"""최소 진단: Python 버전, 필요 표준 모듈, PyQt5, 데이터 디렉터리 쓰기 권한.

기존 구현의 `run.csh --diagnose` / `runtime_check.py` / `verify_environment.py`
역할을 개념만 이어받아 훨씬 단순하게 하나로 합쳤다 (DESIGN.md 2부 3절
"진단과 실행이 한 번의 호출로 끝나는 느낌"). run.csh는 이 모듈을
`python -m smvwp.diagnostics`로 호출해 사전 점검 후 바로 앱을 띄운다.
"""

from __future__ import annotations

import importlib
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


def check_pyqt5() -> dict:
    try:
        from PyQt5 import Qt as _qt  # type: ignore
        from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR  # type: ignore
    except ImportError as exc:
        return {"available": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - 방어적 처리
        return {"available": False, "error": f"PyQt5 import 중 오류: {exc}"}
    return {"available": True, "pyqt_version": PYQT_VERSION_STR, "qt_version": QT_VERSION_STR}


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
    pyqt5_result = check_pyqt5() if include_pyqt5 else {"available": None, "skipped": True}
    data_dir_result = check_data_dir(data_dir)

    modules_ok = all(item["ok"] for item in modules_result.values())
    # PyQt5 실패는 GUI를 못 띄운다는 뜻이지만, --diagnose 자체는 정보 제공이
    # 목적이므로 overall ok 판정에는 Python 버전/모듈/데이터 디렉터리만 반영하고
    # PyQt5는 별도 경고로 취급한다 (CLI 진단은 GUI 없이도 유용해야 함).
    overall_ok = python_result["ok"] and modules_ok and data_dir_result["ok"]

    return {
        "ok": overall_ok,
        "python": python_result,
        "modules": modules_result,
        "pyqt5": pyqt5_result,
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
        lines.append("PyQt5: 점검 생략")
    elif pyqt5.get("available"):
        lines.append(f"PyQt5: {pyqt5['pyqt_version']} / Qt {pyqt5['qt_version']} - OK")
    else:
        lines.append(f"PyQt5: 사용 불가 ({pyqt5.get('error')})")
    data_dir = result["data_dir"]
    if not data_dir["configured"]:
        lines.append("데이터 디렉터리: 미지정 (정상 - 최초 실행 시 GUI에서 지정합니다)")
    elif data_dir["ok"]:
        lines.append(f"데이터 디렉터리: {data_dir['path']} - 쓰기 OK")
    else:
        lines.append(f"데이터 디렉터리: {data_dir['path']} - 오류: {data_dir['error']}")

    lines.append(f"종합 결과: {'OK' if result['ok'] else 'FAIL'}")

    # PyQt5는 종합 판정에 넣지 않는다 (수집 전용 CLI는 PyQt5 없이도 동작해야
    # 하므로). 다만 그대로 두면 "PyQt5 사용 불가 + 종합 OK"가 나란히 찍혀
    # 모순처럼 보이므로, 무엇이 되고 무엇이 안 되는지 한 줄로 못박는다.
    if not pyqt5.get("skipped") and not pyqt5.get("available"):
        lines.append(
            "  └ 단, PyQt5가 없어 GUI는 실행할 수 없습니다. "
            "수집 전용 CLI(smvwp_cli.py collect / scan)는 사용 가능합니다."
        )
    return "\n".join(lines)


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
            "data_dir": {"configured": False, "ok": False, "path": None, "error": None},
        }))
        return 0 if ok else 1

    resolved = paths.resolve_data_dir(args.data_dir)
    result = run_diagnostics(resolved)
    print(format_report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
