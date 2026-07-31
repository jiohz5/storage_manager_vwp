#!/bin/csh -f
# Storage Manager VWP (phase 1) - 단일 진입점.
#
# 기존 저장소 루트의 run.csh와 달리, 여기서는 STORAGE_MANAGER_PYTHON_BIN
# 하나만 지원한다 (REBUILD_CONCEPT.md 3절 결정 - "_HOME" 방식은 없앰,
# 옵션이 하나면 헷갈릴 여지가 없다).
#
# 사용법:
#   setenv STORAGE_MANAGER_PYTHON_BIN /installed/python/3.12.x/bin/python3
#   ./run.csh                 # 진단 후 바로 GUI 실행
#   ./run.csh --diagnose      # 진단만 하고 종료 (GUI 실행 안 함)

set app_dir = "$0:h"
if ("$app_dir" == "$0") set app_dir = "."
cd "$app_dir"
set app_dir = "$cwd"

if (! $?STORAGE_MANAGER_PYTHON_BIN) then
    echo "ERROR: STORAGE_MANAGER_PYTHON_BIN이 설정되지 않았습니다."
    echo "Python 3.12 실행 파일 경로를 지정하세요, 예:"
    echo '  setenv STORAGE_MANAGER_PYTHON_BIN /installed/python/3.12.x/bin/python3'
    exit 2
endif

set python_bin = "$STORAGE_MANAGER_PYTHON_BIN"

if (! -x "$python_bin") then
    echo "ERROR: 지정한 Python 실행 파일을 실행할 수 없습니다: $python_bin"
    exit 2
endif

if ($?PYTHONHOME) then
    echo "WARN: 상속된 PYTHONHOME=$PYTHONHOME 을 무시합니다 (표준 라이브러리 경로 꼬임 방지)."
    unsetenv PYTHONHOME
endif

echo "Storage Manager VWP (phase 1) 시작"
echo "Python 실행 파일: $python_bin"

# 진단과 실행을 한 번의 호출로: 먼저 Python 버전/필수 모듈만 빠르게 점검한다.
"$python_bin" -m smvwp.diagnostics --python-only
if ($status != 0) then
    echo "ERROR: Python 런타임 사전 점검 실패. 위 메시지를 확인하세요."
    exit 2
endif

if ($#argv > 0) then
    if ("$argv[1]" == "--diagnose") then
        # --diagnose는 데이터 디렉터리 쓰기 권한/PyQt5까지 포함한 전체 진단만
        # 하고 GUI는 띄우지 않는다.
        if ($?STORAGE_MANAGER_DATA_DIR) then
            exec "$python_bin" -m smvwp.diagnostics --data-dir "$STORAGE_MANAGER_DATA_DIR"
        else
            exec "$python_bin" -m smvwp.diagnostics
        endif
    endif
endif

if ($?STORAGE_MANAGER_DATA_DIR) then
    echo "데이터 디렉터리: $STORAGE_MANAGER_DATA_DIR (환경변수)"
    exec "$python_bin" "$app_dir/app.py" --data-dir "$STORAGE_MANAGER_DATA_DIR" $argv:q
endif

echo "데이터 디렉터리: 저장된 위치 사용 (없으면 GUI에서 최초 지정 요청)"
exec "$python_bin" "$app_dir/app.py" $argv:q
