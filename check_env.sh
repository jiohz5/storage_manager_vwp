#!/bin/sh
# 환경 점검 - 왜 스캔이 더딘지, 데이터가 위험한 자리에 있는지 한 번에 긁는다.
#
# **읽기만 한다.** 계정 트리를 훑지 않으므로 몇 초 안에 끝나고, 근무 시간에
# 돌려도 스토리지에 부담이 없다. 출력을 그대로 복사해 주면 된다.
#
#   ./check_env.sh                 # 화면으로
#   ./check_env.sh > env.txt 2>&1  # 파일로 받아서 붙여넣기
#
# ## 왜 csh가 아니라 sh인가
#
# 이 저장소의 실행 스크립트(run.csh, setup_cron.csh)는 사내 로그인 셸에 맞춰
# csh다. 이 파일은 앱 실행 경로가 아니라 일회성 진단이고, csh는 리다이렉션과
# 따옴표 처리가 까다로워 진단 스크립트에는 맞지 않는다. shebang이 셸을
# 정하므로 로그인 셸이 csh여도 그냥 실행된다.
#
# ## 왜 곳곳에 timeout이 붙어 있는가
#
# 대상이 NFS다. 마운트가 응답하지 않으면 `df`나 `stat`이 무한정 멈춘다.
# 진단 스크립트가 멈춰 버리면 진단을 못 한다 - 못 읽으면 못 읽었다고 적고
# 넘어가는 편이 낫다.

# 어느 디렉터리에서 실행해도 저장소를 찾도록 스크립트 자신의 위치를 쓴다.
HERE=$(cd "$(dirname "$0")" && pwd)

TO="timeout 10"
command -v timeout >/dev/null 2>&1 || TO=""

line() { printf '\n=== %s ===\n' "$1"; }

# 경로 하나의 파일시스템 종류와 마운트 옵션. NFS 위인지가 핵심이다.
describe_path() {
    _p="$1"
    if [ ! -e "$_p" ]; then
        printf '  %-40s (없거나 접근 불가)\n' "$_p"
        return
    fi
    _fs=$($TO df -T "$_p" 2>/dev/null | awk 'NR==2 {print $2}')
    _mp=$($TO df -P "$_p" 2>/dev/null | awk 'NR==2 {print $6}')
    [ -z "$_fs" ] && _fs="?"
    printf '  %-40s %-8s %s\n' "$_p" "$_fs" "${_mp:-?}"
    if [ -n "$_mp" ] && [ -r /proc/mounts ]; then
        awk -v mp="$_mp" '$2 == mp { print "      옵션: " $4 }' /proc/mounts
    fi
}

printf '########## Storage Manager VWP 환경 점검 ##########\n'
printf '수집 시각: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"

line "1. 장비"
printf '  호스트: %s\n' "$(hostname 2>/dev/null)"
printf '  커널  : %s\n' "$(uname -r 2>/dev/null)"
if [ -r /etc/redhat-release ]; then
    printf '  OS    : %s\n' "$(cat /etc/redhat-release)"
elif [ -r /etc/os-release ]; then
    printf '  OS    : %s\n' "$(. /etc/os-release; echo "$PRETTY_NAME")"
fi
printf '  CPU   : %s코어\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null)"
printf '  부하  : %s\n' "$(cat /proc/loadavg 2>/dev/null)"

line "2. Python / 데이터 디렉터리"
PY="${STORAGE_MANAGER_PYTHON_BIN:-}"
if [ -z "$PY" ]; then
    printf '  STORAGE_MANAGER_PYTHON_BIN 미설정 - python3로 대체 시도\n'
    PY=$(command -v python3 2>/dev/null)
fi
printf '  python: %s\n' "${PY:-없음}"
[ -n "$PY" ] && printf '  버전  : %s\n' "$("$PY" -V 2>&1)"

DATA="${STORAGE_MANAGER_DATA_DIR:-}"
if [ -z "$DATA" ] && [ -r "$HOME/.storage_manager_vwp/data_dir" ]; then
    DATA=$(cat "$HOME/.storage_manager_vwp/data_dir" 2>/dev/null)
    printf '  (환경변수 대신 포인터 파일에서 읽음)\n'
fi
printf '  데이터 디렉터리: %s\n' "${DATA:-미지정}"
if [ -n "$DATA" ]; then
    describe_path "$DATA"
    printf '  DB 파일:\n'
    $TO ls -la "$DATA"/*.db "$DATA"/*.db-wal "$DATA"/*.db-shm 2>/dev/null \
        | sed 's/^/      /' || printf '      (없음)\n'
fi

line "3. 계정 경로"
if [ -n "$PY" ] && [ -n "$DATA" ] && [ -r "$DATA/config.json" ]; then
    "$PY" - "$DATA/config.json" <<'PYEOF' 2>/dev/null | while read -r p; do describe_path "$p"; done
import json, sys
try:
    raw = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
for account in raw.get("accounts", []):
    if account.get("path"):
        print(account["path"])
PYEOF
else
    printf '  config.json을 읽을 수 없어 건너뜁니다\n'
fi

line "4. NFS 클라이언트 설정"
if [ -r /proc/mounts ] && grep -q 'nfs' /proc/mounts 2>/dev/null; then
    printf '  NFS 마운트:\n'
    awk '$3 ~ /^nfs/ { print "      " $2 "  [" $3 "]  " $4 }' /proc/mounts
    printf '\n  RPC 동시 요청 한도 (클수록 병렬 여유):\n'
    for f in /proc/sys/sunrpc/tcp_max_slot_table_entries \
             /proc/sys/sunrpc/tcp_slot_table_entries; do
        [ -r "$f" ] && printf '      %s = %s\n' "$(basename "$f")" "$(cat "$f")"
    done
    if command -v nfsstat >/dev/null 2>&1; then
        printf '\n  nfsstat 클라이언트 요약 (getattr 비중이 높으면 메타데이터 병목):\n'
        $TO nfsstat -c 2>/dev/null | head -20 | sed 's/^/      /'
    else
        printf '\n  nfsstat 없음 (nfs-utils 미설치)\n'
    fi
else
    printf '  NFS 마운트를 찾지 못했습니다\n'
fi

line "5. 도구 지원 여부"
printf '  du   : %s\n' "$(du --version 2>/dev/null | head -1)"
if du --inodes --help >/dev/null 2>&1 || du --help 2>/dev/null | grep -q -- --inodes; then
    printf '         --inodes 지원 O  (계정별 inode를 같은 순회에서 얻을 수 있음)\n'
else
    printf '         --inodes 지원 X\n'
fi
printf '  find : %s\n' "$(find --version 2>/dev/null | head -1)"
printf '  nice : %s / ionice : %s\n' \
    "$(command -v nice >/dev/null 2>&1 && echo 있음 || echo 없음)" \
    "$(command -v ionice >/dev/null 2>&1 && echo 있음 || echo 없음)"
printf '         ※ NFS에서는 ionice가 효과 없습니다 (블록 장치용)\n'
if [ -r /proc/pressure/io ]; then
    printf '  PSI  : 사용 가능\n'
    sed 's/^/      /' /proc/pressure/io
else
    printf '  PSI  : /proc/pressure 없음 (커널이 지원 안 하거나 꺼져 있음)\n'
fi

line "6. cron 등록 상태"
CRON=$(crontab -l 2>/dev/null | grep -i 'smvwp\|storage')
if [ -n "$CRON" ]; then
    printf '%s\n' "$CRON" | sed 's/^/  /'
else
    printf '  (등록된 항목 없음 또는 crontab 조회 불가)\n'
fi

line "7. 앱 진단"
if [ -n "$PY" ]; then
    if [ -n "$DATA" ]; then
        (cd "$HERE" && $TO "$PY" -m smvwp.diagnostics --data-dir "$DATA" 2>&1) | sed 's/^/  /'
    else
        (cd "$HERE" && $TO "$PY" -m smvwp.diagnostics 2>&1) | sed 's/^/  /'
    fi
else
    printf '  python을 찾지 못해 건너뜁니다\n'
fi

line "8. 스캔 진행 진단"
if [ -n "$PY" ]; then
    (cd "$HERE" && $TO "$PY" smvwp_cli.py scan --status 2>&1) | sed 's/^/  /'
else
    printf '  python을 찾지 못해 건너뜁니다\n'
fi

printf '\n########## 끝 ##########\n'
