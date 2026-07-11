#!/bin/csh -f

set app_dir = "$0:h"
if ("$app_dir" == "$0") set app_dir = "."
cd "$app_dir"
set app_dir = "$cwd"

if ($?STORAGE_MANAGER_PYTHON_HOME) then
    set python_bin = "$STORAGE_MANAGER_PYTHON_HOME/bin/python3"
else if ($?PYTHONHOME) then
    set python_bin = "$PYTHONHOME/bin/python3"
else
    set python_bin = "python3"
endif

if ($?STORAGE_MANAGER_DATA_DIR) then
    set data_dir = "$STORAGE_MANAGER_DATA_DIR"
else
    set data_dir = "$app_dir/data"
endif

exec "$python_bin" "$app_dir/nightly_scan.py" --data-dir "$data_dir" --install-cron --python "$python_bin"
