"""야간 상세 스캔의 체크포인트/기준선/실행 이력을 담는 SQLite 저장소.

phase 1의 `store.py`(15분 표본)와는 별도 파일(`detail_scan.db`)을 쓴다 -
책임이 다른 데이터를 한 파일에 섞지 않아야 각 데이터의 보존/정리 정책을
독립적으로 관리하기 쉽고, "자체 비대화 방지"(DESIGN.md 1부 2-5) 예산도 따로
따지기 쉽다.

핵심 개념:
- **체크포인트(scan_checkpoints)**: 디렉터리 하나 = 행 하나. `du`/`find`
  대상이 될 디렉터리 큐를 담는다. 처음엔 계정 루트 바로 아래 최상위
  디렉터리들만 pending으로 깔리고, 시간 초과로 "분할"되면 그 자식
  디렉터리들이 새 pending 행으로 추가된다 (DESIGN.md 1부 7절 "디렉터리 단위
  타임아웃 -> 넘으면 하위 디렉터리로 분할해 재시도"). 중단된 지점은 이
  테이블 상태만으로 항상 복원 가능하다.
- **세대(generation)**: 계정별 "기준선 스캔 한 바퀴"를 세는 정수. 한 세대가
  완전히 끝나야(대기 중인 체크포인트가 하나도 없어야) baseline_results에
  결과를 저장하고 다음 세대로 넘어간다. 증가 경로 비교는 항상 "완료된 세대
  vs 그 이전 완료된 세대"를 같은 경로(path) 기준으로 대조한다 - "어제 top N
  vs 오늘 top N"처럼 순위가 바뀌면 다른 항목이 되어버리는 비교(REVIEW.md가
  지적했던 문제)를 피하기 위함이다.

## 파일 구성

하나에 1,800줄이던 것을 맡은 표에 따라 나눴다. 밖에서는 지금처럼
`scan_store.X` 로 쓴다 - 여기서 공개 이름을 전부 다시 내보낸다.

- `db` - 연결·스키마·공통 상수
- `runs` - 실행 이력과 스캔 중 리소스 시계열
- `checkpoints` - 체크포인트 큐, 계정 상태, 진행률·남은 시간·진단
- `results` - 기준선 결과와 큰 파일, 새로 생긴 과제 판정
- `server` - 서버 부하 표본과 사용 기록
"""

from .db import (
    BASELINE,
    SCHEMA,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_SPLIT,
    connect,
    db_path,
    journal_mode,
    session,
    utc_now_iso,
)
from .runs import (
    finish_run,
    last_runs,
    latest_run,
    load_samples,
    prune_load_samples,
    recent_runs,
    record_parallelism,
    save_load_samples,
    set_current_target,
    start_run,
)
from .checkpoints import (
    AccountDiagnosis,
    AccountScanState,
    ETA_MIN_INTERVALS,
    ETA_SAMPLE_SIZE,
    checkpoint_progress,
    completion_intervals,
    diagnose_account,
    estimate_remaining_seconds,
    failed_count,
    failed_paths,
    get_account_state,
    insert_children,
    is_seeded,
    last_baseline_times,
    last_processed,
    mark_done,
    mark_error,
    mark_generation_completed,
    mark_split,
    next_pending,
    partial_paths,
    recent_checkpoints,
    seed_checkpoints,
)
from .results import (
    NewPaths,
    generation_completed_at,
    generation_paths,
    growth_delta,
    is_confidently_new,
    large_file_changes,
    largest_files,
    measured_total_kb,
    new_paths,
    prune_old_generations,
    save_large_files,
    save_tree_entries,
    top_paths,
    tree_entries,
)
from .server import (
    BUSIEST_BY_CPU,
    BUSIEST_BY_MEMORY,
    SOURCE_COLLECTOR,
    SOURCE_SCAN,
    busiest_processes,
    mount_activity,
    prune_server_samples,
    prune_usage_events,
    save_server_sample,
    server_samples,
    usage_by_action,
    usage_by_user,
    usage_events,
)

# 밖에서 `scan_store.X` 로 쓰는 이름들 (위에서 다시 내보낸 것 전부).
__all__ = [
    "BASELINE",
    "SCHEMA",
    "STATUS_DONE",
    "STATUS_ERROR",
    "STATUS_PENDING",
    "STATUS_SPLIT",
    "connect",
    "db_path",
    "journal_mode",
    "session",
    "utc_now_iso",
    "finish_run",
    "last_runs",
    "latest_run",
    "load_samples",
    "prune_load_samples",
    "recent_runs",
    "record_parallelism",
    "save_load_samples",
    "set_current_target",
    "start_run",
    "AccountDiagnosis",
    "AccountScanState",
    "ETA_MIN_INTERVALS",
    "ETA_SAMPLE_SIZE",
    "checkpoint_progress",
    "completion_intervals",
    "diagnose_account",
    "estimate_remaining_seconds",
    "failed_count",
    "failed_paths",
    "get_account_state",
    "insert_children",
    "is_seeded",
    "last_baseline_times",
    "last_processed",
    "mark_done",
    "mark_error",
    "mark_generation_completed",
    "mark_split",
    "next_pending",
    "partial_paths",
    "recent_checkpoints",
    "seed_checkpoints",
    "NewPaths",
    "generation_completed_at",
    "generation_paths",
    "growth_delta",
    "is_confidently_new",
    "large_file_changes",
    "largest_files",
    "measured_total_kb",
    "new_paths",
    "prune_old_generations",
    "save_large_files",
    "save_tree_entries",
    "top_paths",
    "tree_entries",
    "BUSIEST_BY_CPU",
    "BUSIEST_BY_MEMORY",
    "SOURCE_COLLECTOR",
    "SOURCE_SCAN",
    "busiest_processes",
    "mount_activity",
    "prune_server_samples",
    "prune_usage_events",
    "save_server_sample",
    "server_samples",
    "usage_by_action",
    "usage_by_user",
    "usage_events",
]
