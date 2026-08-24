#!/bin/sh
# 병렬을 몇 개까지 올려도 되는지 실측한다.
#
#   ./bench_parallel.sh /user/a/dir1 /user/a/dir2 /user/b/dir1 ...
#
# 같은 디렉터리 묶음을 동시 실행 수 1, 2, 4, 8로 돌려 벽시계 시간을 잰다.
#
#   speedup = (N x T1) / TN
#     speedup 이 N 에 가까우면  -> 여유 있음, 더 올려도 된다
#     speedup 이 더 안 오르면   -> 그 지점이 포화
#
# ## 캐시 때문에 속지 않으려고
#
# NFS는 속성을 잠깐 캐시하므로 뒤 회차가 공짜로 빨라 보일 수 있다. 그래서
# 마지막에 N=1을 한 번 더 돌린다. 처음 N=1과 비슷하면 캐시 영향이 없는
# 것이고, 훨씬 빠르면 이 측정 전체를 믿으면 안 된다.
#
# 디렉터리는 **1~2분 안에 끝나는 것**으로 고르세요. 너무 크면 측정에만
# 한나절 걸립니다.

[ $# -lt 2 ] && { echo "사용법: $0 <디렉터리> <디렉터리> [...]  (2개 이상)"; exit 1; }

BUDGET=1800   # du 하나당 상한(초). 넘으면 그 회차는 못 믿는 값이 된다.
TO=""
command -v timeout >/dev/null 2>&1 && TO="timeout $BUDGET"

# 동시 실행 수 N으로 인자 전체를 훑고 걸린 초를 돌려준다.
run_round() {
    _n=$1
    _start=$(date +%s)
    _running=0
    for _d in $DIRS; do
        $TO du -sk "$_d" >/dev/null 2>&1 &
        _running=$((_running + 1))
        if [ "$_running" -ge "$_n" ]; then
            wait
            _running=0
        fi
    done
    wait
    echo $(( $(date +%s) - _start ))
}

DIRS="$*"
COUNT=$#
printf '대상 %d개\n' "$COUNT"
for d in $DIRS; do printf '  %s\n' "$d"; done
printf '\n%-8s %10s %10s %s\n' "동시실행" "걸린시간" "speedup" "판정"
printf -- '---------------------------------------------------\n'

BASE=""
for N in 1 2 4 8; do
    [ "$N" -gt "$COUNT" ] && continue
    T=$(run_round "$N")
    [ -z "$BASE" ] && BASE=$T
    if [ "$T" -gt 0 ] && [ "$BASE" -gt 0 ]; then
        # speedup 을 소수 한 자리로 (정수 연산만 사용)
        SP=$(( BASE * 10 / T ))
        SP_TXT="$((SP / 10)).$((SP % 10))x"
        # SP 는 이미 10배 값이다 (2.0x -> 20). N 의 70% 이상이면 여유로 본다:
        #   speedup >= 0.7*N   <=>   SP >= N*7
        if [ "$SP" -ge $((N * 7)) ]; then
            VERDICT="여유 있음"
        elif [ "$SP" -ge $((N * 4)) ]; then
            VERDICT="이득 줄어듦"
        else
            VERDICT="포화"
        fi
    else
        SP_TXT="-"; VERDICT="너무 짧음 - 더 큰 디렉터리로"
    fi
    printf '%-8s %9ss %10s %s\n' "$N" "$T" "$SP_TXT" "$VERDICT"
done

T_CTRL=$(run_round 1)
printf -- '---------------------------------------------------\n'
printf '%-8s %9ss %10s %s\n' "1(재측정)" "$T_CTRL" "-" "캐시 점검"
if [ "$BASE" -gt 0 ] && [ $((T_CTRL * 2)) -lt "$BASE" ]; then
    printf '\n주의: 재측정이 처음보다 2배 이상 빠릅니다. 캐시가 끼어들었으니\n'
    printf '      위 speedup 을 믿지 마세요 (더 큰 디렉터리로 다시).\n'
else
    printf '\n캐시 영향 없음 - 위 값을 그대로 읽으면 됩니다.\n'
fi

printf '\n읽는 법: "여유 있음"이 나온 가장 큰 N 이 안전한 병렬도입니다.\n'
printf '         config.json 의 weekend_parallel_accounts 에 넣으세요.\n'
