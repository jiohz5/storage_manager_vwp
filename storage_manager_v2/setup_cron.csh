#!/bin/csh -f
# 15분 주기 수집을 cron에 등록하는 도우미 스크립트.
#
# GUI를 계속 띄워 두지 않아도 수집이 계속되게 하려면 이 스크립트로 cron 항목을
# 추가한다 (REBUILD_CONCEPT.md 7절 3번 "cron 또는 백그라운드 타이머" 중 cron
# 경로). GUI만 쓰는 경우에는 이 스크립트를 실행하지 않아도 된다 - GUI가 켜져
# 있는 동안은 내부 타이머(smvwp.scheduler)가 동일한 주기로 수집한다.
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

set cron_line = "*/15 * * * * $python_bin $app_dir/collector_cli.py --data-dir $data_dir >> $data_dir/logs/collector_cron.log 2>&1"
set marker = "# storage_manager_vwp_v2_collector"

echo "다음 crontab 항목을 추가합니다:"
echo "$cron_line $marker"

(crontab -l | grep -v "$marker" ; echo "$cron_line $marker") | crontab -
if ($status != 0) then
    echo "ERROR: crontab 등록에 실패했습니다."
    exit 1
endif

echo "완료. 'crontab -l'로 확인할 수 있습니다."
