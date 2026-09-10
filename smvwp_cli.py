#!/usr/bin/env python3
"""Storage Manager VWP 단일 진입점.

    ./smvwp_cli.py gui                     # 관리 GUI (run.csh가 호출)
    ./smvwp_cli.py collect                 # 15분 수집 한 번 (cron)
    ./smvwp_cli.py scan                    # 야간 상세 스캔 (cron, 22:00~06:00)
    ./smvwp_cli.py scan --now              # 시간창 무시하고 지금 실행
    ./smvwp_cli.py scan --stop             # 실행 중인 스캔에 안전 중지 요청
    ./smvwp_cli.py scan --now --parallel 4 # 볼륨 4개 동시 - 부하 실측용
    ./smvwp_cli.py scan --now --engine du # 예전 du 방식으로 한 번만
    ./smvwp_cli.py notify                  # 트레이 알림기 실행
    ./smvwp_cli.py notify --install-autostart

이전에는 하위 명령마다 별도 스크립트(app.py, collector_cli.py,
nightly_scan_cli.py, notifier_cli.py)를 뒀는데, 넷 다 "sys.path를 잡고 모듈을
호출한다"는 같은 껍데기였다. 폐쇄망에는 파일 하나하나가 반입 대상이므로 굳이
넷으로 나눠 둘 이유가 없어 하나로 합쳤다.

**PyQt5는 GUI/트레이 경로에서만 import한다.** cron이 부르는 `collect`와
`scan`은 화면이 없는 환경에서 돌아야 하므로, 모듈 최상단에서 PyQt5를 건드리면
안 된다 (각 핸들러 안에서 늦게 import한다).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 어느 작업 디렉터리에서 호출되더라도 `smvwp` 패키지를 찾을 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smvwp import config as config_module  # noqa: E402
from smvwp import paths  # noqa: E402

# 수집 이력·보고서·알림을 담기에 최소한 이 정도는 있어야 한다는 기준. 여유가
# 이보다 적으면 경고한다 (막지는 않는다 - 폐쇄망에서 선택지가 적을 수 있다).
MIN_RECOMMENDED_FREE_BYTES = 500 * 1024 * 1024  # 500MB

PYQT5_MISSING_MESSAGE = """\
ERROR: PyQt5를 불러올 수 없습니다 ({error}).

지금 쓰고 있는 Python은 다음입니다:

  {python}

이 앱의 화면은 PyQt5만 씁니다 (추가 테마 패키지 없이 자체 QSS를 입힙니다).
사내에 PyQt5가 들어 있는 Python이 따로 있다면 STORAGE_MANAGER_PYTHON_BIN을
그쪽 실행 파일로 다시 지정하세요.

전체 진단은 다음으로 볼 수 있습니다 (GUI 없이 동작합니다):

  ./run.csh --diagnose

GUI 없이 수집만 하려면 아래 하위 명령은 GUI 툴킷 없이도 동작합니다:

  ./smvwp_cli.py collect
  ./smvwp_cli.py load                    # 쌓인 서버 부하 이력 보기
  ./smvwp_cli.py usage                   # 누가 이 도구를 얼마나 쓰는지
  ./smvwp_cli.py scan
"""

DATA_DIR_MISSING_MESSAGE = (
    "ERROR: 데이터 디렉터리를 찾을 수 없습니다. --data-dir을 지정하거나 "
    "STORAGE_MANAGER_DATA_DIR을 설정하거나, GUI를 한 번 실행해 위치를 저장하세요."
)


def _resolve_or_fail(explicit):
    """헤드리스 경로용 데이터 디렉터리 해석.

    cron은 대화형이 아니므로 물어볼 수 없다. 못 찾으면 조용히 기본값을 만들지
    않고 명확히 실패한다 - 엉뚱한 곳에 수집 이력이 쌓이는 것보다 낫다."""

    data_dir = paths.resolve_data_dir(explicit)
    if data_dir is None:
        print(DATA_DIR_MISSING_MESSAGE, file=sys.stderr)
        raise SystemExit(2)
    return data_dir


# -- gui -------------------------------------------------------------------

def _suggestion_text() -> str:
    """쓸 수 있는 후보와 여유 공간을 보여준다.

    폐쇄망에서 관리자가 아니면 데이터를 둘 곳이 몇 군데 없다. 빈 파일 선택창만
    띄우면 어디를 골라야 할지 알 수 없으므로, 확실히 사용자 것인 위치와 그
    여유 공간을 먼저 보여주고 판단하게 한다."""

    lines = []
    for info in paths.suggest_data_dirs():
        free = paths.format_bytes(info.free_bytes)
        state = "쓰기 가능" if info.usable else "쓰기 불가"
        lines.append(f"  {info.path}\n      여유 {free} · {state}")
    if not lines:
        return ""
    return "\n\n쓸 수 있을 만한 위치:\n" + "\n".join(lines)


def _prompt_for_data_dir_gui() -> Path:
    from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

    QApplication.instance() or QApplication(sys.argv)
    QMessageBox.information(
        None,
        "데이터 디렉터리 지정",
        "Storage Manager VWP를 처음 실행합니다.\n"
        "수집 데이터를 저장할, 모니터링 대상과는 분리된 쓰기 가능한 디렉터리를 선택하세요.\n"
        "모니터링 대상이 가득 차도 기록할 수 있도록 다른 파일시스템을 권장합니다."
        + _suggestion_text(),
    )
    selected = QFileDialog.getExistingDirectory(None, "데이터 디렉터리 선택")
    if not selected:
        QMessageBox.critical(None, "데이터 디렉터리 필요", "데이터 디렉터리를 선택하지 않아 종료합니다.")
        raise SystemExit(2)

    # 고른 곳의 여유 공간을 바로 확인해 준다 - 나중에 가득 차서 수집이 멈추는
    # 것보다 지금 아는 편이 낫다.
    info = paths.describe_location(Path(selected))
    if info.free_bytes is not None and info.free_bytes < MIN_RECOMMENDED_FREE_BYTES:
        QMessageBox.warning(
            None,
            "여유 공간 부족",
            f"선택한 위치의 여유 공간이 {paths.format_bytes(info.free_bytes)}뿐입니다.\n"
            f"권장 최소는 {paths.format_bytes(MIN_RECOMMENDED_FREE_BYTES)}입니다.\n"
            "공간이 부족하면 수집과 알림 기록이 멈출 수 있습니다.",
        )
    return Path(selected)


def _resolve_data_dir_interactive(explicit) -> Path:
    resolved = paths.resolve_data_dir(explicit)
    if resolved is not None:
        return resolved
    resolved = _prompt_for_data_dir_gui()
    paths.ensure_writable(resolved)
    paths.remember_data_dir(resolved)
    return resolved


def command_gui(args) -> int:
    """관리 GUI를 띄운다.

    PyQt5가 없을 때 파이썬 스택트레이스만 뜨면 반입된 장비에서 원인을 짚기
    어렵다. GUI가 실제로 필요해지는 이 지점에서 친절히 안내한다."""

    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        print(
            PYQT5_MISSING_MESSAGE.format(error=exc, python=sys.executable),
            file=sys.stderr,
        )
        return 2

    app = QApplication(sys.argv)

    # 위젯을 만들기 전에 테마를 입힌다 - 나중에 적용하면 이미 만들어진
    # 위젯이 기본 외양으로 한 번 그려졌다가 바뀌어 깜빡인다.
    from smvwp.gui.theme import apply as apply_theme

    apply_theme(app)

    try:
        data_dir = _resolve_data_dir_interactive(args.data_dir)
        config = config_module.load_config(data_dir)
    except (paths.DataDirError, config_module.ConfigError) as exc:
        QMessageBox.critical(None, "시작 실패", str(exc))
        return 1

    from smvwp.gui.main_window import MainWindow

    window = MainWindow(data_dir, config)
    window.show()
    # 창이 뜬 뒤에 안내를 띄운다 - 부모 창 없이 모달이 먼저 뜨는 것을 피한다.
    window.show_first_run_if_needed()
    return app.exec_()


def command_collect(args) -> int:
    from smvwp.cycle import run_collection_cycle

    data_dir = _resolve_or_fail(args.data_dir)
    try:
        config = config_module.load_config(data_dir)
    except config_module.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    records = run_collection_cycle(data_dir, config)
    failed = [r for r in records if not r.ok]
    print(f"수집 완료: {len(records)}개 계정, 실패 {len(failed)}건")
    for record in failed:
        print(f"  - {record.account_id}: {record.error_message}")
    return 0


# -- load ------------------------------------------------------------------
#
# 이 명령이 답하려는 것: **낮에 돌려도 되나, 몇 갈래까지 되나, 그때 누가 서버를
# 쓰고 있었나.** 표본을 아무리 쌓아도 꺼내 볼 길이 없으면 판단에 못 쓴다.


def _bar(value, full=100.0, width=20) -> str:
    """숫자 하나를 눈으로 견줄 수 있게. 값이 없으면 빈 칸."""

    if value is None:
        return " " * width
    filled = int(round(min(1.0, max(0.0, value / full)) * width))
    return "#" * filled + "." * (width - filled)


def _num(value, digits=1, suffix="") -> str:
    return "-" if value is None else f"{value:,.{digits}f}{suffix}"


def command_load(args) -> int:
    from smvwp import loadreport, scan_store
    from smvwp.reports import pad
    from datetime import datetime, timedelta, timezone

    data_dir = _resolve_or_fail(args.data_dir)
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    conn = scan_store.connect(data_dir)
    try:
        rows = scan_store.server_samples(conn, since=since, limit=200000)
        if not rows:
            print("아직 서버 부하 표본이 없습니다.")
            print("수집기(15분 주기)가 한 번은 돌아야 쌓이기 시작합니다:")
            print("  ./smvwp_cli.py collect")
            return 0

        print(f"== 서버 부하 최근 {args.days}일 ==  표본 {len(rows):,}벌")
        print()

        # -- 스캔이 돌 때와 안 돌 때 --------------------------------------
        #
        # "스캔이 서버를 얼마나 흔드나"에 답하는 가장 곧은 방법 - 같은 서버의
        # 두 상태를 나란히 놓는 것.
        split = loadreport.split_by_scan(rows)
        print("[스캔 중 vs 평소]  (CPU 는 우리를 뺀 나머지 기준)")
        print(
            pad("", 12) + pad("표본", 8, ">") + pad("CPU평균", 10, ">")
            + pad("CPU최고", 10, ">") + pad("load", 8, ">")
            + pad("대기중", 9, ">") + pad("NFS ops/s", 12, ">")
        )
        for key in ("without_scan", "with_scan"):
            bucket = split[key]
            print(
                pad(bucket.label, 12)
                + pad(f"{bucket.samples:,}", 8, ">")
                + pad(_num(bucket.others_cpu.average, 1, "%"), 10, ">")
                + pad(_num(bucket.others_cpu.peak, 1, "%"), 10, ">")
                + pad(_num(bucket.load_avg.average), 8, ">")
                + pad(_num(bucket.blocked_others.average), 9, ">")
                + pad(_num(bucket.nfs_ops.average, 0), 12, ">")
            )
        print()

        # -- 시간대별 --------------------------------------------------------
        buckets = loadreport.hourly_profile(rows)
        print("[시간대별]  다른 작업이 쓰는 CPU (우리 제외)")
        for bucket in buckets:
            # 우리 스캔이 섞인 칸을 표시하지 않으면 "새벽은 한가하다"를
            # 그대로 믿게 되는데, 사실은 그 한가함 안에 우리가 들어 있다.
            mark = "" if bucket.scan_free else "  <- 우리 스캔 섞임"
            print(
                "  " + pad(bucket.label, 6)
                + _bar(bucket.others_cpu.average) + "  "
                + pad(_num(bucket.others_cpu.average, 1, "%"), 8, ">")
                + "  표본 " + pad(f"{bucket.samples:,}", 5, ">") + mark
            )
        print()

        # 우리 스캔이 돌던 시간대는 후보에서 뺀다 - 새벽이 한가한 것은 이미
        # 우리가 그 자리를 쓰고 있어서라, "여기에 더 얹으세요"가 되지 않는다.
        #
        # 제목을 "여유가 있는 시간대"라고 달지 않는다. 가장 한가한 축에 든
        # 시간대라도 여유가 없을 수 있는데, 그러면 제목이 약속한 것과 아래
        # 줄들이 서로 어긋난다. 순위는 순위대로 보여 주고 판단은 줄마다 붙인다.
        quiet = loadreport.quietest_hours(buckets, limit=3)
        print("[다른 작업이 가장 적었던 시간대]  (우리 스캔이 없던 때만)")
        if not quiet:
            print("  아직 판단할 만큼 쌓이지 않았습니다.")
            print("  수집기가 며칠 더 돌아야 시간대별 표본이 모입니다.")
        else:
            for bucket in quiet:
                verdict = loadreport.daytime_verdict(bucket)
                mark = "여유 있음" if verdict.room else "여유 없음"
                print(
                    f"  {verdict.label}  {mark} - {verdict.reason}"
                    f"  (다른 작업 CPU {_num(verdict.others_cpu, 1, '%')},"
                    f" 표본 {verdict.samples})"
                )
            if not any(loadreport.daytime_verdict(item).room for item in quiet):
                print()
                print("  가장 한가한 시간대에도 여유가 없습니다 - 낮 스캔은 권하지 않습니다.")
        print()

        # -- 그때 무엇이 돌았나 ----------------------------------------------
        by = scan_store.BUSIEST_BY_MEMORY if args.by == "mem" else scan_store.BUSIEST_BY_CPU
        busiest = scan_store.busiest_processes(conn, since=since, by=by, limit=args.limit)
        if busiest:
            label = "메모리" if args.by == "mem" else "CPU"
            print(f"[서버를 쓴 작업]  {label} 큰 것부터")
            print(
                "  " + pad("사용자", 12) + pad("작업", 18)
                + pad("CPU최고", 10, ">") + pad("CPU평균", 10, ">")
                + pad("메모리최고", 13, ">") + pad("표본", 8, ">")
            )
            for item in busiest:
                mine = "  (우리)" if item["is_ours"] else ""
                print(
                    "  " + pad(str(item["user_name"] or "-"), 12)
                    + pad((item["comm"] or "-")[:16], 18)
                    + pad(_num(item["cpu_peak"], 0, "%"), 10, ">")
                    + pad(_num(item["cpu_avg"], 0, "%"), 10, ">")
                    + pad(_num((item["rss_peak"] or 0) / 1024, 0, "MB"), 13, ">")
                    + pad(f"{item['samples']:,}", 8, ">") + mine
                )
            print()
            print("  CPU 평균은 **상위 목록에 들었던 표본들만의 평균**입니다.")
            print("  한가할 때는 목록에 못 들어 빠지므로 하루 평균보다 높게 나옵니다.")
            print()

        # -- NFS ------------------------------------------------------------
        mounts = scan_store.mount_activity(conn, since=since)
        if mounts:
            print("[NFS 마운트]")
            print(
                "  " + pad("마운트", 26) + pad("ops합", 13, ">")
                + pad("ops/s평균", 12, ">") + pad("왕복ms", 10, ">")
                + pad("대기ms", 10, ">")
            )
            for item in mounts:
                print(
                    "  " + pad(str(item["mount_point"])[:24], 26)
                    + pad(f"{int(item['total_ops'] or 0):,}", 13, ">")
                    + pad(_num(item["ops_avg"], 0), 12, ">")
                    + pad(_num(item["rtt_avg"], 2), 10, ">")
                    + pad(_num(item["queue_avg"], 2), 10, ">")
                )
            print()
            print("  대기(queue)가 왕복(rtt)보다 크면 병목은 파일서버가 아니라")
            print("  이쪽 RPC 슬롯입니다 - 그때 병렬을 줄이면 정확히 반대 처방입니다.")
    finally:
        conn.close()
    return 0


# -- usage -----------------------------------------------------------------
#
# 이 도구를 어떻게 안착시킬지가 아직 미지수다 - 몇 명이 쓸지, 무엇을 보러
# 들어오는지, 스캔을 직접 돌리는 사람이 있는지. 감으로 정하면 아무도 안 쓰는
# 기능에 공을 들이게 된다.


def command_usage(args) -> int:
    from smvwp import formatting, scan_store
    from smvwp.reports import pad
    from datetime import datetime, timedelta, timezone

    data_dir = _resolve_or_fail(args.data_dir)
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    conn = scan_store.connect(data_dir)
    try:
        people = scan_store.usage_by_user(conn, since=since)
        actions = scan_store.usage_by_action(conn, since=since)
        if not people:
            print(f"최근 {args.days}일 사용 기록이 없습니다.")
            print("GUI 를 한 번도 열지 않았거나, 이 기능이 붙기 전 기간입니다.")
            return 0

        print(f"== 사용 현황 최근 {args.days}일 ==  쓴 사람 {len(people)}명")
        print()

        print("[사람별]")
        print(
            "  " + pad("사용자", 14) + pad("횟수", 8, ">") + pad("쓴 날", 8, ">")
            + "  " + pad("처음", 20) + pad("마지막", 20)
        )
        for row in people:
            print(
                "  " + pad(str(row["user_name"] or "-"), 14)
                + pad(f"{row['events']:,}", 8, ">")
                + pad(f"{row['days']:,}", 8, ">")
                + "  " + pad(formatting.local_minute_text(row["first_seen"]), 20)
                + pad(formatting.local_minute_text(row["last_seen"]), 20)
            )
        print()
        print("  '쓴 날'이 횟수보다 중요합니다. 하루에 열 번 연 사람과 열흘 동안")
        print("  매일 한 번 연 사람은 횟수가 같아도 전혀 다른 이야기입니다 -")
        print("  뒤쪽만이 이 도구가 일과에 들어갔다는 뜻입니다.")
        print()

        print("[무엇을 하러 들어오나]")
        print(
            "  " + pad("행동", 18) + pad("횟수", 8, ">") + pad("쓴 사람", 9, ">")
            + "  " + pad("마지막", 20)
        )
        for row in actions:
            print(
                "  " + pad(str(row["action"]), 18)
                + pad(f"{row['events']:,}", 8, ">")
                + pad(f"{row['users']:,}", 9, ">")
                + "  " + pad(formatting.local_minute_text(row["last_seen"]), 20)
            )
        print()
        print("  쓴 사람 수를 함께 봅니다. 한 사람이 백 번 쓴 기능과 열 사람이")
        print("  열 번씩 쓴 기능은 같은 숫자라도 뜻이 다릅니다.")

        if args.recent:
            print()
            print("[최근 기록]")
            for row in scan_store.usage_events(conn, since=since, limit=args.recent):
                detail = f"  {row['detail']}" if row["detail"] else ""
                print(
                    "  " + pad(formatting.local_minute_text(row["happened_at"]), 20)
                    + pad(str(row["user_name"] or "-"), 14)
                    + str(row["action"]) + detail
                )
    finally:
        conn.close()
    return 0


# -- scan ------------------------------------------------------------------

def command_scan(args) -> int:
    from smvwp import nightly_scan

    data_dir = _resolve_or_fail(args.data_dir)

    if args.stop:
        if nightly_scan.request_stop(data_dir):
            print("중지를 요청했습니다. 실행 중인 스캔이 다음 체크포인트에서 안전하게 멈춥니다.")
            return 0
        print("현재 실행 중인 야간 스캔이 없습니다.")
        return 1

    try:
        config = config_module.load_config(data_dir)
    except config_module.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.status:
        return _print_scan_status(data_dir, config)

    # 설정 파일을 고치지 않고 이번 실행만 다른 엔진으로 잰다. 두 방법을 같은
    # 밤에 번갈아 돌려 비교할 때 쓴다.
    if args.engine:
        config.settings.scan_engine = args.engine

    summary = nightly_scan.run_nightly_scan(
        data_dir,
        config,
        triggered_by="terminal" if args.now else "cron",
        bypass_window=args.now,
        parallel_accounts=args.parallel,
        # cron 으로 뜬 야간 실행만 기다린다. 낮에 사람이 눌러 둔 스캔이 아직
        # 돌고 있으면 그것이 야간 창을 보고 물러나는데, 재던 디렉터리를 마저
        # 끝내느라 시간이 걸린다. 여기서 안 기다리면 그날 밤을 통째로 잃는다.
        lock_wait_seconds=0.0 if args.now else nightly_scan.DEFAULT_LOCK_WAIT_SECONDS,
    )

    if not summary.started:
        print(f"실행하지 않음: {summary.reason}")
        return 0

    # 동시 계정 수가 왜 그 값인지를 같이 적는다 - 숫자만 보면 주말이라 3인
    # 것인지 설정이 잘못된 것인지 구분이 안 된다.
    night = "주말 밤" if summary.weekend_night else "평일 밤"
    print(
        f"야간 상세 스캔 종료 (run_id={summary.run_id}, 상태={summary.status}, "
        f"{night} · 동시 볼륨={summary.parallel_accounts} · "
        f"엔진={config.settings.scan_engine})"
    )
    for outcome in summary.accounts:
        print(
            f"  - {outcome.account_name}: 기준선 세대 {outcome.baseline_generation} "
            f"({outcome.baseline_status})"
        )
    return 0 if summary.status in (nightly_scan.STATUS_COMPLETED, nightly_scan.STATUS_PAUSED) else 1


def _fmt_seconds(value) -> str:
    if value is None:
        return "-"
    if value < 60:
        return f"{value:.0f}초"
    if value < 3600:
        return f"{value / 60:.1f}분"
    return f"{value / 3600:.1f}시간"


def _print_scan_status(data_dir, config) -> int:
    """스캔을 돌리지 않고 "지금 어디까지 갔고 왜 더딘지"를 출력한다.

    화면과 보고서는 "얼마나 갔나"만 보여 준다. 밤새 돌았는데 한 계정만 갔을 때
    필요한 것은 **왜 안 갔나**이고, 그 답은 대개 두 숫자에 있다 - 분할로 생긴
    작업 비율과 체크포인트 하나에 걸린 시간.
    """

    from smvwp import formatting, scan_store

    # 어느 엔진으로 재고 있는지부터 적는다. 설정 파일을 열어 보지 않으면 알 수
    # 없었는데, "지금 du 로 도는 건가 순회로 도는 건가"는 속도를 이야기할 때
    # 가장 먼저 나오는 질문이다.
    engine = config.settings.scan_engine
    engine_note = "파이썬 순회" if engine == config_module.SCAN_ENGINE_PYTHON else "du 실행"
    print(f"측정 엔진  {engine} ({engine_note})")
    print()

    conn = scan_store.connect(data_dir)
    try:
        run = scan_store.latest_run(conn)
        if run is None:
            print("아직 실행된 스캔이 없습니다.")
            return 0

        keys = run.keys()
        parallel = run["parallel_accounts"] if "parallel_accounts" in keys else None
        weekend = run["weekend_night"] if "weekend_night" in keys else None
        night = "-"
        if weekend is not None:
            night = "주말 밤" if weekend else "평일 밤"
        print(f"최근 실행  {run['run_id']}  상태={run['status']}")
        started = formatting.local_datetime_text(run["started_at"])
        ended = formatting.local_datetime_text(run["ended_at"])
        print(f"  시작 {started}  종료 {ended}")
        print(f"  {night} · 동시 볼륨 {parallel or 1}개")
        print()

        header = (
            f"{'계정':<20}{'완료':>7}{'대기':>7}{'분할':>7}{'실패':>7}"
            f"{'분할로생긴작업':>16}{'중앙시간':>11}{'최장':>11}"
        )
        print(header)
        print("-" * 92)

        for account in config.accounts:
            state = scan_store.get_account_state(conn, account.account_id)
            diag = scan_store.diagnose_account(
                conn, account.account_id, state.working_generation
            )
            if not diag.total:
                print(f"{account.name:<20}{'아직 시작 안 함':>7}")
                continue
            print(
                f"{account.name:<20}{diag.done:>7,}{diag.pending:>7,}"
                f"{diag.split:>7,}{diag.error:>7,}"
                f"{diag.from_split:>10,} ({diag.split_ratio * 100:>3.0f}%)"
                f"{_fmt_seconds(diag.median_seconds):>11}"
                f"{_fmt_seconds(diag.slowest_seconds):>11}"
            )
            if diag.last_path:
                print(f"{'':<20}마지막: {diag.last_path}")

        print()
        budget = config.settings.detail_task_timeout_seconds
        print(f"디렉터리 하나당 시간 예산: {_fmt_seconds(budget)}")
        print()
        print("읽는 법")
        print("  · '분할로 생긴 작업' 비율이 높다 = 시간 초과가 반복됐다는 뜻입니다.")
        print("    분할하면 그 서브트리를 **처음부터 다시** 걷습니다 - 여기가 높으면")
        print("    같은 파일을 여러 번 세고 있는 것이라, 시간 예산을 늘리는 편이")
        print("    오히려 빠릅니다 (du -k는 시간 초과가 나도 그때까지 출력한 것을")
        print("    저장하므로, 예산을 늘려도 잃는 것이 없습니다).")
        print("  · '중앙 시간'이 시간 예산에 가깝다 = 대부분이 예산을 다 쓰고 잘렸다는")
        print("    뜻입니다. 같은 결론입니다.")
        print("  · 평일 밤은 동시 계정이 1개입니다 - 한 계정이 밤을 다 쓰면 나머지는")
        print("    그날 스캔되지 않습니다 (다음 밤에 순서가 돌아갑니다).")
    finally:
        conn.close()
    return 0


# -- notify ----------------------------------------------------------------

def command_notify(args) -> int:
    from smvwp import notifier

    return notifier.main_with_args(args)


# -- 진입점 -----------------------------------------------------------------

def command_probe(args) -> int:
    """지정 경로를 여러 각도로 재고 **짧은 코드**로 요약한다.

    폐쇄망에서 사람이 화면을 보고 옮겨 적는다는 전제로 만들었다. 그래서 기본
    출력이 세 줄이고, 상세 표는 그 아래에 참고용으로만 붙는다.
    """

    from smvwp import probe

    seconds = args.seconds if args.seconds else probe.DEFAULT_SLICE_SECONDS
    target = Path(args.path).expanduser()
    if not target.is_dir():
        print(f"ERROR: 디렉터리가 아닙니다: {target}", file=sys.stderr)
        return 1

    accounts = []
    volume_key = None
    needs_peers = not args.no_peers and not (args.peer and args.peer2)
    if needs_peers:
        # 이웃 계정을 찾으려면 설정이 필요하다. 없어도 진단 자체는 돌아간다 -
        # 그 경우 L·M 이 0(재지 못함)으로 남을 뿐이다. 진단을 통째로 못 돌리는
        # 것보다 그 두 칸이 비는 편이 낫다.
        data_dir = paths.resolve_data_dir(args.data_dir)
        if data_dir is None:
            print("(데이터 디렉터리를 몰라 이웃 계정 비교는 건너뜁니다. "
                  "--peer/--peer2 로 직접 줄 수 있습니다)")
        else:
            try:
                accounts = config_module.load_config(data_dir).accounts
                volume_key = paths.volume_key
            except Exception as exc:  # 설정이 깨졌어도 진단은 계속한다
                print(f"(설정을 읽지 못해 이웃 계정 비교는 건너뜁니다: {exc})")

    say = (lambda message: None) if args.codes_only else print
    if not args.codes_only:
        sweeps = (
            probe.QUICK_THREAD_SWEEP + probe.QUICK_PROCESS_SWEEP if args.quick
            else probe.THREAD_SWEEP + probe.PROCESS_SWEEP
        )
        pieces = len(sweeps) + 1
        pairs = 3 * 5
        print(f"대상: {target}")
        print(
            f"예상 소요: 전체 순회 1회 + du 2회(앞뒤) + 조각 {pieces + pairs}개"
            f"(조각당 {seconds:.0f}초) ≈ 순회시간 + du시간 + "
            f"{int((pieces + pairs) * seconds) // 60}분"
        )
        print("이 명령은 아무것도 쓰지 않습니다 (읽기 전용).")
        print("-" * 60)

    result = probe.run_probe(
        str(target),
        accounts=accounts,
        volume_key=volume_key,
        slice_seconds=seconds,
        workers=args.workers,
        quick=args.quick,
        process_threads=args.process_threads,
        peer_same_filer=args.peer,
        peer_other_filer=args.peer2,
        log=say,
    )

    if not args.codes_only:
        print("-" * 60)
    print(probe.format_transfer(result))
    if not args.codes_only:
        print(probe.format_detail(result))
        print("")
        print("위의 CODE / CPU / VAL 줄만 옮겨 주시면 됩니다 (줄 이름도 함께).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smvwp_cli.py", description="Storage Manager VWP"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_data_dir(target):
        target.add_argument(
            "--data-dir",
            help="데이터 디렉터리 (미지정 시 STORAGE_MANAGER_DATA_DIR 또는 저장된 위치)",
        )

    gui = sub.add_parser("gui", help="관리 GUI 실행")
    _add_data_dir(gui)
    gui.set_defaults(func=command_gui)


    collect = sub.add_parser("collect", help="15분 경량 수집 1회 (cron용)")
    _add_data_dir(collect)
    collect.set_defaults(func=command_collect)

    load = sub.add_parser("load", help="쌓인 서버 부하 이력 보기")
    _add_data_dir(load)
    load.add_argument(
        "--days", type=int, default=7, help="며칠치를 볼지 (기본 7)"
    )
    load.add_argument(
        "--by",
        choices=["cpu", "mem"],
        default="cpu",
        help="작업 목록 정렬 기준 (기본 cpu)",
    )
    load.add_argument(
        "--limit", type=int, default=15, help="작업 목록 줄 수 (기본 15)"
    )
    load.set_defaults(func=command_load)

    usage = sub.add_parser("usage", help="누가 이 도구를 얼마나 쓰는지")
    _add_data_dir(usage)
    usage.add_argument("--days", type=int, default=30, help="며칠치를 볼지 (기본 30)")
    usage.add_argument(
        "--recent", type=int, default=0, help="최근 기록을 N줄 함께 보기"
    )
    usage.set_defaults(func=command_usage)

    scan = sub.add_parser("scan", help="야간 상세 스캔 (cron용)")
    _add_data_dir(scan)
    scan.add_argument(
        "--now",
        action="store_true",
        help="시간창(22:00~06:00)을 무시하고 지금 실행 - 터미널 직접 실행용 진단/복구 경로",
    )
    scan.add_argument("--stop", action="store_true", help="실행 중인 스캔에 안전 중지 요청")
    scan.add_argument(
        "--status",
        action="store_true",
        help="스캔을 돌리지 않고 진행 상황만 진단해서 출력 (왜 더딘지 포함)",
    )
    scan.add_argument(
        "--parallel",
        type=int,
        default=None,
        metavar="N",
        help=(
            "서로 다른 볼륨 N개를 동시에 스캔한다 (기본: 설정값, 보통 1=직렬). "
            "같은 볼륨의 계정은 이 값과 무관하게 하나씩 돈다 - 같은 볼륨을 "
            "여럿이 두들겨 봐야 빨라지지 않는다. 결과는 보고서의 "
            "'스캔 중 리소스 변화'에서 확인한다."
        ),
    )
    scan.add_argument(
        "--engine",
        choices=config_module.SCAN_ENGINES,
        default=None,
        help=(
            "이번 실행만 다른 측정 엔진을 쓴다 (기본: 설정값). "
            "python=파이썬 순회(빠름), du=du -k 실행(nice/ionice가 듣는다). "
            "설정 파일은 건드리지 않는다."
        ),
    )
    scan.set_defaults(func=command_scan)

    probe = sub.add_parser(
        "probe",
        help="지정 경로를 여러 각도로 재고 짧은 진단 코드로 요약 (읽기 전용)",
    )
    _add_data_dir(probe)
    probe.add_argument("--path", required=True, help="진단 대상 디렉터리")
    probe.add_argument(
        "--seconds", type=float, default=None,
        help="시간을 잘라 재는 조각 하나의 길이(초). 기본 15",
    )
    probe.add_argument(
        "--workers", type=int, default=4,
        help="순회 스레드 수 (기본 4 - 야간 스캔이 쓰는 값과 같게)",
    )
    probe.add_argument(
        "--quick", action="store_true",
        help="스레드/프로세스 확장을 x1/x4만 본다 (시간 절반)",
    )
    probe.add_argument(
        "--process-threads", type=int, default=2,
        help=(
            "프로세스 측정에서 프로세스마다 쓸 스레드 수 (기본 2). "
            "프로세스 수 × 이 값이 총 동시 요청 수가 된다."
        ),
    )
    probe.add_argument(
        "--peer", default=None,
        help="같은 파일러의 다른 볼륨 경로 (미지정 시 계정 목록에서 자동으로 찾음)",
    )
    probe.add_argument(
        "--peer2", default=None,
        help="다른 파일러의 경로 (미지정 시 계정 목록에서 자동으로 찾음)",
    )
    probe.add_argument(
        "--no-peers", action="store_true",
        help="이웃 계정 비교(L·M)를 아예 건너뛴다",
    )
    probe.add_argument(
        "--codes-only", action="store_true",
        help="진행 로그 없이 옮겨 적을 세 줄만 출력",
    )
    probe.set_defaults(func=command_probe)

    notify = sub.add_parser("notify", help="트레이 알림기")
    _add_data_dir(notify)
    notify.add_argument("--poll-seconds", type=int, default=60)
    notify.add_argument("--install-autostart", action="store_true", help="로그인 시 자동 시작 등록")
    notify.add_argument("--remove-autostart", action="store_true", help="로그인 자동 시작 해제")
    notify.add_argument("--status", action="store_true", help="미확인 알림 수만 출력하고 종료")
    notify.set_defaults(func=command_notify)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
