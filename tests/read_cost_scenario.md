# 측정 시나리오 - NFS 위에서 창이 읽는 비용 (grokbot VM)

## 무엇을 알고 싶나

1. **잠금 묶기(93e6e02)가 NFS 에서 실제로 얼마였나.** 스캔 상태 한 번에 SQL 문이
   120여 개라 문마다 잡던 공유 잠금을 한 번의 읽기 트랜잭션으로 묶었다. 로컬
   디스크에서는 차이가 안 보여 NFS 에서만 잴 수 있다. 이 숫자로 "계정마다 묻던
   쿼리를 GROUP BY 로 더 줄일지" 를 정한다 (DESIGN.md '남은 것' 첫 두 줄).
2. 2026-10-16 에 올린 다섯 가지(93e6e02, d5cadd9, ca08194, 4806b1d, 23a2d92)가
   NFS 에서도 로컬에서 잰 만큼 줄었나.

## 견줄 코드 셋

| 이름 | 커밋 | 무엇 |
|---|---|---|
| A | `f4d35cc` | 다섯 가지 전 |
| B | `93e6e02` | A + 잠금 묶기만 |
| C | `main` | 다섯 가지 전부 |

A 와 B 를 견주면 잠금 묶기 하나의 효과, A 와 C 를 견주면 전부의 효과가 나온다.
스크립트는 언제나 `main` 의 `tests/read_cost.py` 하나를 쓰고, 잴 코드만 `--code` 로
바꾼다 (A·B 에는 이 스크립트의 새 기능이 없다).

## 준비

- 조용한 VM 에서 잰다. 'NFS요청' 칸은 마운트 전체의 값이라, 그 마운트에서 cron 이나
  스캔이 돌면 섞인다 (`crontab -l` 로 이 도구의 줄이 없는지 본다).
- 데이터는 시나리오 1~7 에 쓴 **NFS 마운트 아래 새 디렉터리**에 만든다.

```bash
cd <게스트의 저장소> && git fetch && git checkout main && git pull
git worktree add /tmp/smvwp-A f4d35cc
git worktree add /tmp/smvwp-B 93e6e02
M=<NFS 마운트>/smvwp-measure          # 새 디렉터리 (아래 실행마다 그 밑에 하나씩)
mkdir -p "$M" results
stat -f -c %T "$M"                     # nfs 여야 한다
grep " $(df --output=target "$M" | tail -1) " /proc/mounts   # vers=, 옵션
```

## 잰다

캐시를 비우고(`--cold`) 두 번씩, 버전을 번갈아 돈다 (시간이 흐르며 생기는 차이를 섞는다).
한 번에 데이터를 새로 만들므로(표본 DB 80MB, 스캔 DB 240MB 쯤) NFS 에서는 몇 분 걸린다.

```bash
for round in 1 2; do
  for v in A B C; do
    code=/tmp/smvwp-$v; [ "$v" = C ] && code=$PWD
    python3 tests/read_cost.py "$M/$v-cold-$round" --code "$code" --cold | tee "results/$v-cold-$round.txt"
  done
done
for v in A B C; do      # 캐시가 데워진 채로 한 번씩 (같은 장비에서 다시 읽는 경우)
  code=/tmp/smvwp-$v; [ "$v" = C ] && code=$PWD
  python3 tests/read_cost.py "$M/$v-warm" --code "$code" | tee "results/$v-warm.txt"
done
```

### (선택) NFS 서버가 같은 VM 이면 지연을 넣어 한 번 더

루프백 NFS 는 왕복이 거의 0 이라 잠금 효과가 안 보인다. 실기의 파일서버 왕복은
0.2~1ms 쯤이다. NFS 서버가 같은 VM 일 때만, 끝나면 **반드시 지운다**:

```bash
sudo tc qdisc add dev lo root netem delay 0.5ms
for v in A B; do code=/tmp/smvwp-$v; python3 tests/read_cost.py "$M/$v-delay" --code "$code" --cold | tee "results/$v-delay.txt"; done
sudo tc qdisc del dev lo root
```

(NFS 서버가 다른 호스트면 `lo` 대신 그 쪽 인터페이스이고, `ping -c 5 <서버>` 로 왕복만 적어 주면 된다.)

## 보고

- `results/*.txt` 전부 (머리말에 커밋·Python·SQLite·마운트 옵션·저널 모드가 있다).
- 아래 표를 버전별로 채워서 (cold 두 번의 평균):

| 경로 | A 초 / NFS요청 | B 초 / NFS요청 | C 초 / NFS요청 |
|---|---|---|---|
| 스캔 탭 다음 읽기 (30초마다) | | | |
| 스캔 중 다음 읽기 (5초마다) | | | |
| 홈 첫 읽기 | | | |
| 홈 다음 읽기 (15분마다) | | | |
| 계정 상세 창 | | | |

- NFS 버전 (v3 면 잠금이 lockd 로 가서 'NFS요청' 에 안 잡힌다 - 그때는 '초' 로만 본다).
- 측정 디렉터리 `$M` 과 worktree(`git worktree remove /tmp/smvwp-A` 등)는 보고 뒤 정리해도 된다.

## 무엇을 기대하나

- **A -> B**: '스캔 탭 다음 읽기', '스캔 중 다음 읽기' 의 NFS요청(v4 면 LOCK/LOCKU)이
  120 대에서 한 자리로, 초도 그만큼 준다. 줄지 않으면 묶기가 NFS 에서 듣지 않는 것이라
  이유를 따로 봐야 한다.
- **A -> C**: '홈 다음 읽기' 읽은MB 18 -> 0.5, '계정 상세 창' 44 -> 2, '홈 첫 읽기'
  는 C 에서 21 쯤 (C 의 '홈 첫 읽기 (밤 스캔이 대조를 남기기 전)' 줄은 업데이트 뒤 첫날의
  값). A 의 홈 첫 읽기는 **경로 길이에 따라** 크게 달라진다 - 경로가 긴 로컬 임시
  디렉터리에서는 138MB, grokbot 의 NFS 마운트에서는 92MB 였다.
- **정하는 법**: C 에서 '스캔 중 다음 읽기' 가 cold 로 0.1초 아래면 SQL 문 수는 더 줄이지
  않는다. 0.3초를 넘으면 계정마다 묻던 쿼리(진행 수, 실패, 잰 용량, 남은 시간)를
  GROUP BY 로 묶는다.

## 결과 (2026-10-17)

세 기대 모두 맞았다 - NFS v4.2, 루프백 서버, 에뮬레이션 CPU. 표와 판정(SQL 문 수는
더 줄이지 않는다)은 DESIGN.md "NFS 위에서 창이 느렸던 이유" 의 'NFS 실측' 에 있다.
캐시를 데운 채로 잰 값도 비운 채로 잰 값과 같았다 - 리눅스 NFS 클라이언트는 파일
잠금을 잡을 때마다 그 파일의 캐시를 버린다.
