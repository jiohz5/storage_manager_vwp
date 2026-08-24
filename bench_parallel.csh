#!/bin/csh -f
# 병렬을 몇 개까지 올려도 되는지 실측한다.
#
#   ./bench_parallel.csh /user/a/dir1 /user/b/dir1 /user/c/dir1 /user/d/dir1
#
# 같은 디렉터리 묶음을 동시 실행 1, 2, 4, 8로 돌려 벽시계 시간을 잰다.
#
#   speedup = (N x T1) / TN
#     N 의 70% 이상  -> 여유 있음, 더 올려도 된다
#     40% 미만       -> 포화
#
# 마지막에 N=1을 한 번 더 돌린다. 처음보다 2배 이상 빠르면 NFS 속성 캐시가
# 끼어든 것이라 그 측정 전체를 믿으면 안 된다.
#
# 디렉터리는 **1~2분 안에 끝나는 것**으로 고르세요. 경로에 공백이 있으면
# csh가 단어를 쪼개므로 쓸 수 없습니다.

if ($#argv < 2) then
    echo "사용법: $0 <디렉터리> <디렉터리> [...]  (2개 이상)"
    exit 1
endif

set budget = 1800
set have_timeout = 0
which timeout >& /dev/null
if ($status == 0) set have_timeout = 1

set dirs = ($argv)
set count = $#argv

echo "대상 ${count}개"
foreach d ($dirs)
    echo "  $d"
end
echo ""
echo "동시실행   걸린시간   speedup   판정"
echo "---------------------------------------------"

set base = 0
set rounds = (1 2 4 8 1)
set idx = 0

foreach n ($rounds)
    @ idx++

    # 마지막 항목은 캐시 점검용 재측정이다. 그 앞 회차들은 대상 수를 넘으면
    # 의미가 없으므로 건너뛴다 (4개인데 8 동시는 4 동시와 같다).
    set is_control = 0
    if ($idx == $#rounds) set is_control = 1
    if ($is_control == 0 && $n > $count) continue

    set start = `date +%s`
    set running = 0
    foreach d ($dirs)
        if ($have_timeout == 1) then
            timeout $budget du -sk "$d" >& /dev/null &
        else
            du -sk "$d" >& /dev/null &
        endif
        @ running++
        if ($running >= $n) then
            wait
            set running = 0
        endif
    end
    wait
    set now = `date +%s`
    @ t = $now - $start

    if ($is_control == 1) then
        echo "---------------------------------------------"
        echo "1(재측정)  ${t}s        -         캐시 점검"
        @ doubled = $t * 2
        if ($base > 0 && $doubled < $base) then
            echo ""
            echo "주의: 재측정이 처음보다 2배 이상 빠릅니다. 캐시가 끼었으니"
            echo "      위 speedup 을 믿지 마세요 (더 큰 디렉터리로 다시)."
        else
            echo ""
            echo "캐시 영향 없음 - 위 값을 그대로 읽으면 됩니다."
        endif
        break
    endif

    if ($base == 0) set base = $t

    if ($t > 0 && $base > 0) then
        # speedup 을 10배 정수로 들고 다닌다 (csh 는 정수 연산만 한다).
        @ sp = ( $base * 10 ) / $t
        @ whole = $sp / 10
        @ frac = $sp % 10
        @ hi = $n * 7
        @ lo = $n * 4
        if ($sp >= $hi) then
            set verdict = "여유 있음"
        else if ($sp >= $lo) then
            set verdict = "이득 줄어듦"
        else
            set verdict = "포화"
        endif
        echo "$n          ${t}s        ${whole}.${frac}x      $verdict"
    else
        echo "$n          ${t}s        -         너무 짧음 - 더 큰 디렉터리로"
    endif
end

echo ""
echo '읽는 법: "여유 있음"이 나온 가장 큰 N 이 안전한 병렬도입니다.'
echo "         config.json 의 weekend_parallel_accounts 에 넣으세요."
