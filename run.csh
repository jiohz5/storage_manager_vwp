#!/bin/csh -f

set app_dir = "$0:h"
if ("$app_dir" == "$0") set app_dir = "."
cd "$app_dir"
set app_dir = "$cwd"

set python_source = "PATH"
if ($?STORAGE_MANAGER_PYTHON_BIN) then
    set python_bin = "$STORAGE_MANAGER_PYTHON_BIN"
    set python_source = "STORAGE_MANAGER_PYTHON_BIN"
else if ($?STORAGE_MANAGER_PYTHON_HOME) then
    set python_bin = "$STORAGE_MANAGER_PYTHON_HOME/bin/python3"
    set python_source = "STORAGE_MANAGER_PYTHON_HOME"
else
    set python_bin = "python3"
endif

if ("$python_source" != "PATH" && ! -x "$python_bin") then
    echo "ERROR: Python executable is not available: $python_bin"
    echo "Set STORAGE_MANAGER_PYTHON_BIN to python3 or STORAGE_MANAGER_PYTHON_HOME to its installation prefix."
    exit 2
endif

if ($?PYTHONHOME) then
    echo "WARN: Ignoring inherited PYTHONHOME=$PYTHONHOME"
    unsetenv PYTHONHOME
endif

echo "Storage Manager startup"
echo "Python selector: $python_source"
echo "Python executable: $python_bin"
"$python_bin" "$app_dir/runtime_check.py" --python-only
if ($status != 0) then
    echo "ERROR: Python runtime preflight failed."
    exit 2
endif

if ($#argv > 0) then
    if ("$argv[1]" == "--diagnose") then
        if ($?STORAGE_MANAGER_DATA_DIR) then
            set data_dir = "$STORAGE_MANAGER_DATA_DIR"
        else
            set data_dir = "`"$python_bin" "$app_dir/runtime_check.py" --resolve-data-dir --allow-missing`"
        endif
        if ("$data_dir" == "") then
            "$python_bin" "$app_dir/runtime_check.py"
            exit $status
        endif
        "$python_bin" "$app_dir/runtime_check.py" --data-dir "$data_dir"
        if ($status != 0) exit $status
        exec "$python_bin" "$app_dir/verify_environment.py" --data-dir "$data_dir" --monitored-root /user
    endif
endif

if ($?STORAGE_MANAGER_DATA_DIR) then
    echo "Data directory: $STORAGE_MANAGER_DATA_DIR (environment)"
    exec "$python_bin" "$app_dir/app.py" --data-dir "$STORAGE_MANAGER_DATA_DIR" $argv:q
endif

set saved_data_dir = "`"$python_bin" "$app_dir/runtime_check.py" --resolve-data-dir --allow-missing`"
if ("$saved_data_dir" != "") then
    echo "Data directory: $saved_data_dir (saved global location)"
else
    echo "Data directory: not configured; the GUI will ask for one writable global path."
endif
exec "$python_bin" "$app_dir/app.py" $argv:q
