#!/bin/csh -f
# 15분 주기 수집 + 야간 상세 스캔을 cron에 등록하는 도우미 스크립트.
#
# GUI를 계속 띄워 두지 않아도 수집/스캔이 계속되게 하려면 이 스크립트로 cron
# 항목을 추가한다. GUI만 쓰는 경우에는 이 스크립트를 실행하지 않아도 된다 -
# GUI가 켜져 있는 동안은 내부 타이머(smvwp.scheduler)가 15분 수집을 대신하고,
# 야간 상세 스캔은 대시보드의 "지금 야간 스캔 실행" 버튼으로 수동 실행할 수
# 있다 (다만 그 경우 GUI를 계속 켜 둬야 한다).
#
# 야간 상세 스캔(nightly_scan_cli.py)은 cron이 22:00에 딱 한 번 띄우면 그
# 프로세스가 내부적으로 계정을 돌아가며 처리하다가 06:00 시간창이 끝나거나
# 안전 중지 요청이 오면 스스로 멈춘다 (REBUILD_CONCEPT.md 8절 1번,
# CONCEPT.md 3절의 22:00~06:00 정책을 그대로 계승). 강제로 죽이고 싶으면
# `nightly_scan_cli.py --stop`을 쓴다 - 절대 kill/PID로 직접 죽이지 않는다.
#
# 사용법:
#   setenv STORAGE_MANAGER_PYTHON_BIN /installed/python/3.12.x/bin/python3
#   setenv STORAGE_MANAGER_DATA_DIR /path/to/data
#   ./setup_cron.csh

set app_dir = "$0:h"
if ("$app_dir" == "$0") set app_dir = "."
cd "$app_dir"
set app_dir = "$cwd"

if (! $?STORAGE_MANAGER_PYTHON_BIN) then
    echo "ERROR: STORAGE_MANAGER_PYTHON_BIN이 설정되지 않았습니다."
    exit 2
endif
if (! $?STORAGE_MANAGER_DATA_DIR) then
    echo "ERROR: STORAGE_MANAGER_DATA_DIR이 설정되지 않았습니다."
    echo "(cron은 대화형 세션이 아니므로 데이터 디렉터리를 환경변수로 명시해야 합니다.)"
    exit 2
endif

set python_bin = "$STORAGE_MANAGER_PYTHON_BIN"
set data_dir = "$STORAGE_MANAGER_DATA_DIR"
mkdir -p "$data_dir/logs"

set collector_line = "*/15 * * * * $python_bin $app_dir/collector_cli.py --data-dir $data_dir >> $data_dir/logs/collector_cron.log 2>&1"
set collector_marker = "# storage_manager_vwp_v2_collector"

set nightly_line = "0 22 * * * $python_bin $app_dir/nightly_scan_cli.py --data-dir $data_dir >> $data_dir/logs/nightly_scan_cron.log 2>&1"
set nightly_marker = "# storage_manager_vwp_v2_nightly_scan"

echo "다음 crontab 항목을 추가합니다:"
echo "$collector_line $collector_marker"
echo "$nightly_line $nightly_marker"

(crontab -l | grep -v "$collector_marker" | grep -v "$nightly_marker" ; \
    echo "$collector_line $collector_marker" ; \
    echo "$nightly_line $nightly_marker") | crontab -
if ($status != 0) then
    echo "ERROR: crontab 등록에 실패했습니다."
    exit 1
endif

echo "완료. 'crontab -l'로 확인할 수 있습니다."
echo "야간 스캔을 지금 당장 안전하게 멈추려면:"
echo "  $python_bin $app_dir/nightly_scan_cli.py --data-dir $data_dir --stop"
