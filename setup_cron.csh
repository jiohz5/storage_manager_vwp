#!/bin/csh -f
# 15분 주기 수집 + 야간 상세 스캔을 cron에 등록하는 도우미 스크립트.
#
# GUI를 계속 띄워 두지 않아도 수집/스캔이 계속되게 하려면 이 스크립트로 cron
# 항목을 추가한다. GUI만 쓰는 경우에는 이 스크립트를 실행하지 않아도 된다 -
# GUI가 켜져 있는 동안은 내부 타이머(smvwp.scheduler)가 15분 수집을 대신하고,
# 야간 상세 스캔은 대시보드의 "지금 야간 스캔 실행" 버튼으로 수동 실행할 수
# 있다 (다만 그 경우 GUI를 계속 켜 둬야 한다).
#
# 야간 상세 스캔(`smvwp_cli.py scan`)은 cron이 22:00에 딱 한 번 띄우면 그
# 프로세스가 내부적으로 계정을 돌아가며 처리하다가 06:00 시간창이 끝나거나
# 안전 중지 요청이 오면 스스로 멈춘다 (REBUILD_CONCEPT.md 8절 1번,
# CONCEPT.md 3절의 22:00~06:00 정책을 그대로 계승). 강제로 죽이고 싶으면
# `smvwp_cli.py scan --stop`을 쓴다 - 절대 kill/PID로 직접 죽이지 않는다.
#
# 사용법:
#   setenv STORAGE_MANAGER_PYTHON_BIN /installed/python/3.12.x/bin/python3
#   setenv STORAGE_MANAGER_DATA_DIR /path/to/data
#   ./setup_cron.csh
#
# GUI 가 밤을 지키게 하려면(`gui_auto_nightly_scan`) 야간 줄이 있으면 안 된다 -
# cron 이 22시에 또 띄우면 "창을 닫으면 멈춘다"가 성립하지 않는다. 그때는:
#
#   ./setup_cron.csh --collector-only
#
# 15분 수집만 등록하고 야간 줄은 (있으면) 지운다. 수집까지 GUI 에 맡기면 창을
# 닫은 동안 사용량 이력이 끊겨 예측이 무너지므로, 수집은 cron 에 두는 편이 낫다.

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

set collector_only = 0
if ($#argv > 0) then
    if ("$argv[1]" == "--collector-only") set collector_only = 1
endif

set python_bin = "$STORAGE_MANAGER_PYTHON_BIN"
set data_dir = "$STORAGE_MANAGER_DATA_DIR"
mkdir -p "$data_dir/logs"

set collector_line = "*/15 * * * * $python_bin $app_dir/smvwp_cli.py collect --data-dir $data_dir >> $data_dir/logs/collector_cron.log 2>&1"
set collector_marker = "# storage_manager_vwp_v2_collector"

# `nice`/`ionice` 를 여기에 붙인다. 예전에는 `du` 를 띄울 때 붙였는데, 엔진이
# 파이썬 순회로 바뀌면서 별도 프로세스가 사라져 그 접두사가 듣지 않는다.
# 둘 다 없는 장비면 cron 줄이 통째로 실패하므로, 있을 때만 붙인다.
set prefix = ""
which nice >& /dev/null
if ($status == 0) set prefix = "nice -n 10 "
which ionice >& /dev/null
if ($status == 0) set prefix = "${prefix}ionice -c2 -n7 "

set nightly_line = "0 22 * * * ${prefix}$python_bin $app_dir/smvwp_cli.py scan --data-dir $data_dir >> $data_dir/logs/nightly_scan_cron.log 2>&1"
set nightly_marker = "# storage_manager_vwp_v2_nightly_scan"

echo "다음 crontab 항목을 추가합니다:"
echo "$collector_line $collector_marker"
if ($collector_only) then
    echo "(야간 스캔 줄은 등록하지 않습니다 - 있으면 지웁니다. GUI 가 밤을 지키는 설정입니다.)"
else
    echo "$nightly_line $nightly_marker"
endif

# 기존 줄은 표식으로 먼저 지운다. 그래야 여러 번 돌려도 중복되지 않는다.
if ($collector_only) then
    (crontab -l | grep -v "$collector_marker" | grep -v "$nightly_marker" ; \
        echo "$collector_line $collector_marker") | crontab -
else
    (crontab -l | grep -v "$collector_marker" | grep -v "$nightly_marker" ; \
        echo "$collector_line $collector_marker" ; \
        echo "$nightly_line $nightly_marker") | crontab -
endif
if ($status != 0) then
    echo "ERROR: crontab 등록에 실패했습니다."
    exit 1
endif

echo "완료. 'crontab -l'로 확인할 수 있습니다."
echo "야간 스캔을 지금 당장 안전하게 멈추려면:"
echo "  $python_bin $app_dir/smvwp_cli.py scan --data-dir $data_dir --stop"
