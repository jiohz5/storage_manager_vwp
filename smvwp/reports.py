"""일간/주간 보고서와 정리(cleanup) 후보 보고서.

DESIGN.md 1부 2-1의 가장 중요한 원칙: **자동 삭제 없음**. 이 모듈은 어떤 경우에도
파일을 지우거나 지우는 명령을 만들지 않는다. "지워도 될 것 같은 후보"를 사람이
검토할 수 있게 텍스트로 나열할 뿐이다. 그래서 함수 이름도 `delete_*`가 아니라
`build_cleanup_candidates`다.

정리 후보 3조건 (모두 만족해야 후보):
1. 충분히 크다 (`cleanup_min_size_kb` 이상)
2. 충분히 오래 관찰했다 (처음 본 지 `cleanup_min_age_days` 이상)
3. 최근에 변한 적이 없다 (`cleanup_idle_days` 동안 크기 변화 없음)

3번이 특히 중요하다 - 크기만 보고 후보로 올리면 "지금 한창 쓰는 큰 데이터"가
후보에 오른다. 관찰 이력이 짧으면 아예 후보로 올리지 않는다(2번). 확신이 없으면
후보로 올리지 않는 쪽을 택한다.

보고서는 텍스트 파일이다. 폐쇄망에서 별도 뷰어 없이 `cat`으로 볼 수 있어야
하고, 사내 반출 절차에도 텍스트가 가장 무난하기 때문이다. 언어별로 따로 저장해
나중에 다른 언어로도 볼 수 있게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import config as config_module
from . import i18n, scan_store, store, tiers

DAILY = "daily"
WEEKLY = "weekly"
CLEANUP = "cleanup"


def reports_dir(data_dir: Path) -> Path:
    return data_dir / "reports"


def report_path(data_dir: Path, kind: str, day: date, language: str) -> Path:
    return reports_dir(data_dir) / kind / f"{day.isoformat()}_{language}.txt"


def latest_path(data_dir: Path, kind: str, language: str) -> Path:
    return reports_dir(data_dir) / f"latest_{kind}_{language}.txt"


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8", newline="\n")
    temp_path.replace(path)
    return path


def _fmt_kb(size_kb: Optional[int]) -> str:
    if size_kb is None:
        return "-"
    value = float(size_kb)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            return f"{int(value):,} KB" if unit == "KB" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} PB"  # pragma: no cover


def _fmt_pct(pct: Optional[float]) -> str:
    return f"{pct:.1f}%" if pct is not None else i18n.t("common.unknown_value")


# -- 일간 / 주간 -----------------------------------------------------------

def build_daily_report(
    data_dir: Path, config: config_module.AppConfig, now: Optional[datetime] = None
) -> str:
    now = now or datetime.now(timezone.utc)
    conn = store.connect(data_dir)
    try:
        latest = store.latest_samples(conn)
    finally:
        conn.close()

    lines: List[str] = []
    lines.append(f"Storage Manager VWP - {i18n.t('reports.daily')}")
    lines.append(f"{now.isoformat(timespec='seconds')}")
    lines.append("=" * 72)
    lines.append("")
    lines.append(i18n.t("dashboard.df_caveat"))
    lines.append("")

    if not config.accounts:
        lines.append(i18n.t("dashboard.no_accounts"))
        return "\n".join(lines) + "\n"

    # 심각한 계정을 위로 - 보고서를 위에서부터 읽으면 급한 것부터 보이게.
    def sort_key(account):
        sample = latest.get(account.account_id)
        return -tiers.severity(sample.overall_tier) if sample else 1

    header = (
        f"{'계정':<18} {'등급':<10} {'용량':>8} {'inode':>8} {'quota':>8}  파일시스템"
        if i18n.get_language() == i18n.KOREAN
        else f"{'Account':<18} {'Tier':<10} {'Cap.':>8} {'inode':>8} {'quota':>8}  Filesystem"
    )
    lines.append(header)
    lines.append("-" * 72)

    warn_or_worse = 0
    for account in sorted(config.accounts, key=sort_key):
        sample = latest.get(account.account_id)
        if sample is None:
            lines.append(f"{account.name:<18} {i18n.t('dashboard.not_collected')}")
            continue
        if not sample.ok:
            lines.append(
                f"{account.name:<18} {i18n.t('dashboard.collect_failed', message=sample.error_message)}"
            )
            continue
        if tiers.is_at_least(sample.overall_tier, tiers.WARN):
            warn_or_worse += 1
        quota_text = _fmt_pct(sample.quota_pct) if sample.quota_pct is not None else "-"
        lines.append(
            f"{account.name:<18} {tiers.label(sample.overall_tier):<10} "
            f"{_fmt_pct(sample.byte_pct):>8} {_fmt_pct(sample.inode_pct):>8} {quota_text:>8}  "
            f"{sample.filesystem or '-'}"
        )

    lines.append("-" * 72)
    if warn_or_worse:
        lines.append(i18n.t("dashboard.warn_summary", count=warn_or_worse, worst=""))
    else:
        lines.append(i18n.t("dashboard.all_normal", count=len(config.accounts)))

    # 상세 스캔이 밤새 어디까지 갔는지를 여기에 붙인다.
    #
    # 주간이 아니라 **일간**에 넣는 이유: 아침에 보고서를 여는 목적이 대개
    # "밤새 얼마나 갔나"인데, 주간에만 있으면 그걸 주 1회만 볼 수 있다.
    # 주간 보고서는 일간을 통째로 포함하므로 여기 한 번만 넣으면 양쪽에서
    # 다 보인다.
    _append_scan_section(lines, data_dir, config)
    return "\n".join(lines) + "\n"


# 보고서에 적을 실패 경로 최대 개수. 전부 적으면 계정 하나가 보고서를 뒤덮는다
# (수천 개가 나올 수 있다). 개수는 정확히 적고 목록만 자른다.
FAILED_PATHS_IN_REPORT = 50


def _append_failures(lines: List[str], failed: List[tuple], total: int) -> None:
    """재지 못한 경로를 사유와 함께 적는다.

    사유를 같이 적어야 사용자가 다음 행동(권한 요청 / 대상 제외 / 진짜 오류
    신고)을 고를 수 있다. 개수만 적으면 아무것도 못 한다."""

    if not total:
        return
    lines.append(f"    {i18n.t('reports.scan_failed_header', count=total)}")
    for path, message in failed:
        first_line = (message or "").strip().splitlines()[0] if message else "-"
        lines.append(f"      {path}")
        lines.append(f"        {first_line}")
    if total > len(failed):
        lines.append(f"      ... {total - len(failed)}")


def _scan_label(completed_at, fallback_generation=None) -> str:
    """보고서에서 스캔을 가리키는 이름 (날짜 우선).

    GUI의 `widgets.scan_label`과 같은 규칙이지만, 보고서 생성은 Qt 없이
    돌아야 하므로(cron 경로) 여기 따로 둔다."""

    if completed_at:
        text = str(completed_at)
        year, month, day = text[:4], text[5:7], text[8:10]
        if year and month and day:
            if i18n.get_language() == i18n.KOREAN:
                return f"{year[2:]}{month}{day}"
            return f"{year}-{month}-{day}"
    if fallback_generation is not None:
        return i18n.t("scan.nth", n=fallback_generation)
    return i18n.t("common.none")


def _append_scan_section(lines: List[str], data_dir: Path, config: config_module.AppConfig) -> None:
    """계정별 `du` 진행 상황과 마지막으로 처리한 경로.

    개수만 적으면 얼마나 남았는지는 알아도 **어느 대목에서 멈췄는지**는 모른다.
    밤새 돌다 끊긴 스캔을 아침에 볼 때 필요한 것은 후자다 - 경로를 보면 다음
    밤에 어디부터 이어질지 짐작할 수 있다.
    """

    if not config.accounts:
        return

    try:
        conn = scan_store.connect(data_dir)
    except Exception:  # pragma: no cover - 스캔 DB가 없어도 일간 보고서는 나와야 한다
        return

    try:
        body: List[str] = []
        for account in config.accounts:
            state = scan_store.get_account_state(conn, account.account_id)
            entry: List[str] = []
            _append_progress(entry, conn, account.account_id, state.working_generation)
            if not entry:
                continue
            body.append(f"- {account.name}")
            body.extend(entry)

        if body:
            lines.append("")
            lines.append(i18n.t("reports.scan_progress_heading"))
            lines.append("-" * 72)
            lines.extend(body)
    finally:
        conn.close()


def _append_progress(lines: List[str], conn, account_id: str, generation: int) -> None:
    """`du` 진행 개수와 마지막으로 처리한 경로를 적는다.

    보고서를 아침에 여는 이유는 대개 "밤새 얼마나 갔나"이다. 개수만 적으면
    얼마나 남았는지는 알아도 **어느 대목에서 멈췄는지**는 모른다. 경로를 같이
    적어야 다음 밤에 어디부터 이어질지 짐작할 수 있다.
    """

    counts = scan_store.checkpoint_progress(conn, account_id, scan_store.BASELINE, generation)
    if not counts["total"]:
        return

    done = counts["done"] + counts["error"]
    percent = int(done * 100 / counts["total"]) if counts["total"] else 0
    lines.append(
        f"    {i18n.t('reports.scan_progress', done=done, total=counts['total'], percent=percent, pending=counts['pending'])}"
    )

    last = scan_store.last_processed(conn, account_id, scan_store.BASELINE, generation)
    if last is not None:
        when = str(last["scanned_at"])[:19].replace("T", " ")
        size = _fmt_kb(last["size_kb"]) if last["size_kb"] is not None else "-"
        lines.append(
            f"    {i18n.t('reports.scan_last_path', when=when, size=size)}"
        )
        lines.append(f"      {last['path']}")


def build_weekly_report(
    data_dir: Path, config: config_module.AppConfig, now: Optional[datetime] = None
) -> str:
    """주간 보고서 - 일간 현황에 더해 완료된 기준선 세대 간 증가 경로를 붙인다."""

    now = now or datetime.now(timezone.utc)
    lines = [build_daily_report(data_dir, config, now).rstrip("\n"), "", "=" * 72, ""]
    lines.append(f"[{i18n.t('reports.weekly')}] {i18n.t('scan.section_title')}")
    lines.append("")

    conn = scan_store.connect(data_dir)
    try:
        for account in config.accounts:
            state = scan_store.get_account_state(conn, account.account_id)
            current = state.last_completed_generation

            # 재지 못한 경로를 먼저 적는다. 이걸 빼면 "기준선 없음"만 남아서,
            # 스캔이 돌긴 했는데 전부 실패했다는 사실이 보고서에서 사라진다.
            failed = scan_store.failed_paths(
                conn, account.account_id, state.working_generation, limit=FAILED_PATHS_IN_REPORT
            )
            failed_total = scan_store.failed_count(
                conn, account.account_id, state.working_generation
            )

            if not current:
                # scan.no_baseline 문구가 이미 계정명으로 시작한다 - 앞에 또 붙이면
                # "project_a: project_a: ..."가 된다.
                lines.append(f"- {i18n.t('scan.no_baseline', account=account.name)}")
                _append_failures(lines, failed, failed_total)
                continue
            previous = current - 1 if current > 1 else None
            rows = (
                scan_store.growth_delta(
                    conn, account.account_id, current, previous,
                    config.settings.detail_scan_top_n,
                    max_depth=config.settings.growth_list_max_depth,
                )
                if previous
                else scan_store.top_paths(
                    conn, account.account_id, current, config.settings.detail_scan_top_n,
                    max_depth=config.settings.growth_list_max_depth,
                )
            )
            scan_at = scan_store.generation_completed_at(conn, account.account_id, current)
            lines.append(
                f"- {i18n.t('reports.scan_heading', account=account.name, run=_scan_label(scan_at, current))}"
            )
            _append_failures(lines, failed, failed_total)
            if not rows:
                lines.append("    -")
                continue
            if previous:
                previous_at = scan_store.generation_completed_at(
                    conn, account.account_id, previous
                )
                lines.append(
                    f"    {i18n.t('reports.scan_compared_with', previous=_scan_label(previous_at, previous))}"
                )
            for row in rows:
                keys = row.keys()
                if "current_kb" in keys:
                    current_kb = row["current_kb"]
                    previous_kb = row["previous_kb"]
                    if previous_kb is None:
                        delta_text = i18n.t("scan.new_path")
                    else:
                        diff = current_kb - previous_kb
                        delta_text = (
                            i18n.t("scan.no_change") if diff == 0
                            else f"{'+' if diff > 0 else '-'}{_fmt_kb(abs(diff))}"
                        )
                    lines.append(f"    {_fmt_kb(current_kb):>12}  {delta_text:<24} {row['path']}")
                else:
                    lines.append(f"    {_fmt_kb(row['size_kb']):>12}  {'':<24} {row['path']}")
    finally:
        conn.close()

    return "\n".join(lines) + "\n"


# -- 정리 후보 -------------------------------------------------------------

@dataclass
class CleanupCandidate:
    account_name: str
    path: str
    size_kb: int
    first_seen_generation: int
    generations_observed: int
    unchanged: bool


def build_cleanup_candidates(
    data_dir: Path, config: config_module.AppConfig
) -> List[CleanupCandidate]:
    """정리 후보를 고른다. **아무 것도 지우지 않는다** - 목록만 만든다.

    세대별 기준선(baseline_results)을 경로 기준으로 대조해, 충분히 크고 여러
    세대에 걸쳐 크기가 그대로인 경로만 후보로 올린다. 관찰 세대가 하나뿐이면
    "변하지 않았다"고 말할 근거가 없으므로 후보에서 제외한다.
    """

    settings = config.settings
    candidates: List[CleanupCandidate] = []

    conn = scan_store.connect(data_dir)
    try:
        for account in config.accounts:
            state = scan_store.get_account_state(conn, account.account_id)
            current = state.last_completed_generation
            if not current:
                continue

            rows = conn.execute(
                "SELECT generation, path, size_kb FROM baseline_results "
                "WHERE account_id = ? ORDER BY generation",
                (account.account_id,),
            ).fetchall()

            by_path: Dict[str, List[tuple]] = {}
            for row in rows:
                by_path.setdefault(row["path"], []).append((row["generation"], row["size_kb"]))

            for path, history in by_path.items():
                generations = [gen for gen, _ in history]
                if current not in generations:
                    # 최신 세대에 없다 = 이미 사라졌거나 이름이 바뀐 경로.
                    continue
                if len(history) < 2:
                    # 관찰 이력이 한 세대뿐이면 "변화 없음"을 주장할 수 없다.
                    continue
                sizes = [size for _, size in history]
                latest_size = history[-1][1]
                if latest_size is None or latest_size < settings.cleanup_min_size_kb:
                    continue
                if len(set(sizes)) != 1:
                    continue  # 한 번이라도 크기가 변했으면 후보 아님
                candidates.append(
                    CleanupCandidate(
                        account_name=account.name,
                        path=path,
                        size_kb=latest_size,
                        first_seen_generation=generations[0],
                        generations_observed=len(history),
                        unchanged=True,
                    )
                )
    finally:
        conn.close()

    candidates.sort(key=lambda item: item.size_kb, reverse=True)
    return candidates


def build_cleanup_report(data_dir: Path, config: config_module.AppConfig) -> str:
    candidates = build_cleanup_candidates(data_dir, config)
    settings = config.settings

    lines: List[str] = []
    lines.append(f"Storage Manager VWP - {i18n.t('reports.cleanup')}")
    lines.append("=" * 72)
    lines.append("")
    if i18n.get_language() == i18n.KOREAN:
        lines.append("※ 이 보고서는 검토용 목록일 뿐이며 어떤 파일도 삭제하지 않습니다.")
        lines.append(
            f"   조건: {_fmt_kb(settings.cleanup_min_size_kb)} 이상 · 2세대 이상 관찰 · 크기 변화 없음"
        )
    else:
        lines.append("* This report is a review list only. Nothing is ever deleted automatically.")
        lines.append(
            f"   Criteria: >= {_fmt_kb(settings.cleanup_min_size_kb)}, observed 2+ generations, size unchanged"
        )
    lines.append("")

    if not candidates:
        lines.append(
            "조건을 만족하는 정리 후보가 없습니다."
            if i18n.get_language() == i18n.KOREAN
            else "No cleanup candidates met the criteria."
        )
        return "\n".join(lines) + "\n"

    for item in candidates:
        lines.append(
            f"{_fmt_kb(item.size_kb):>12}  {item.account_name:<16} "
            f"(gen {item.first_seen_generation}~, {item.generations_observed})  {item.path}"
        )
    return "\n".join(lines) + "\n"


# -- 생성 진입점 -----------------------------------------------------------

def generate(
    data_dir: Path,
    config: config_module.AppConfig,
    kinds: Optional[List[str]] = None,
    now: Optional[datetime] = None,
    languages: Optional[List[str]] = None,
) -> Dict[str, Path]:
    """보고서를 만들어 파일로 저장하고 {종류: 경로}를 반환한다.

    언어를 지정하지 않으면 지원하는 모든 언어로 만든다 - 나중에 다른 언어로
    보고 싶을 때 다시 계산하지 않아도 되고, 사내 공유 시에도 편하다. 반환하는
    경로는 현재 언어 기준이다."""

    now = now or datetime.now(timezone.utc)
    kinds = kinds or [DAILY, CLEANUP]
    languages = languages or i18n.available_languages()
    original_language = i18n.get_language()
    day = now.date()
    result: Dict[str, Path] = {}

    builders = {
        DAILY: lambda: build_daily_report(data_dir, config, now),
        WEEKLY: lambda: build_weekly_report(data_dir, config, now),
        CLEANUP: lambda: build_cleanup_report(data_dir, config),
    }

    try:
        for language in languages:
            i18n.set_language(language)
            for kind in kinds:
                builder = builders.get(kind)
                if builder is None:
                    continue
                text = builder()
                path = _write_text(report_path(data_dir, kind, day, language), text)
                _write_text(latest_path(data_dir, kind, language), text)
                if language == original_language:
                    result[kind] = path
    finally:
        i18n.set_language(original_language)

    prune_old_reports(data_dir, config.settings.report_retention_days, now)
    return result


def last_weekly_due_date(config: config_module.AppConfig, now: datetime) -> date:
    """오늘로부터 거슬러 올라가 가장 최근의 '주간 보고서 요일' 날짜."""

    target = config.settings.weekly_report_weekday
    day = now.date()
    return day - timedelta(days=(day.weekday() - target) % 7)


def _has_report_on_or_after(data_dir: Path, kind: str, since: date, language: str) -> bool:
    directory = reports_dir(data_dir) / kind
    if not directory.exists():
        return False
    for path in directory.glob(f"*_{language}.txt"):
        try:
            made = date.fromisoformat(path.stem.split("_")[0])
        except ValueError:
            continue
        if made >= since:
            return True
    return False


def due_kinds(
    data_dir: Path, config: config_module.AppConfig, now: Optional[datetime] = None
) -> List[str]:
    """지금 만들어야 할 보고서 종류.

    **이미 만든 것은 다시 만들지 않는다** - 15분마다 불려도 파일 존재 확인만
    하고 끝난다.

    주간은 "오늘이 그 요일인가"가 아니라 **"가장 최근 그 요일 이후로 만든 적이
    있는가"**로 판단한다. 당일에만 만들면 그날 스캔이 못 돌거나 장비가 꺼져
    있었을 때 그 주 보고서가 영영 생기지 않는다 - 밀렸으면 따라잡아야 한다.
    """

    now = now or datetime.now(timezone.utc)
    language = i18n.get_language()
    kinds: List[str] = []

    if not report_path(data_dir, DAILY, now.date(), language).exists():
        kinds.append(DAILY)
        kinds.append(CLEANUP)

    if not _has_report_on_or_after(
        data_dir, WEEKLY, last_weekly_due_date(config, now), language
    ):
        kinds.append(WEEKLY)
    return kinds


def ensure_scheduled(
    data_dir: Path, config: config_module.AppConfig, now: Optional[datetime] = None
) -> Dict[str, Path]:
    """밀린 보고서가 있으면 만든다. 없으면 아무 일도 하지 않는다.

    **수집 경로(15분 주기)에서 부르는 것이 핵심이다.** 예전에는 야간 상세
    스캔이 끝날 때만 만들었는데, 스캔은 시간창 밖이거나 잠금이 잡혀 있으면
    아예 시작하지 않고 그대로 돌아간다. 그러면 일간 보고서까지 함께 사라졌다 -
    정작 일간 보고서 내용은 df 표본 요약이라 스캔과 아무 상관이 없는데도.
    """

    kinds = due_kinds(data_dir, config, now)
    if not kinds:
        return {}
    return generate(data_dir, config, kinds=kinds, now=now)


def should_build_weekly(config: config_module.AppConfig, now: datetime) -> bool:
    return now.weekday() == config.settings.weekly_report_weekday


def prune_old_reports(
    data_dir: Path, retention_days: int, now: Optional[datetime] = None
) -> int:
    """보존 기간이 지난 날짜별 보고서를 지운다 (`latest_*`는 남긴다).

    지우는 대상은 이 앱이 만든 파생 산출물뿐이다 - 모니터링 대상 계정에는
    손대지 않는다."""

    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).date()
    removed = 0
    for kind in (DAILY, WEEKLY, CLEANUP):
        directory = reports_dir(data_dir) / kind
        if not directory.exists():
            continue
        for path in directory.glob("*.txt"):
            day_part = path.stem.split("_")[0]
            try:
                day = date.fromisoformat(day_part)
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
                removed += 1
    return removed
