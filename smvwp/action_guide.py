"""'무엇부터 할까' 한 줄을 **상황 · 이유 · 할 일 · 근거**로 풀어 준다 (Qt 없음).

## 왜 필요한가

우선순위 카드의 한 줄은 "무엇이" 만 말한다 - `layout_proj 96% - 정리하면
800GB 빕니다`. 그 줄에 커서를 올리면 반응하는데 눌러도 아무 일이 없었다.
사람이 거기서 알고 싶은 것은 그 다음이다:

- **지금 어떤 상황인가** - 숫자 몇 개로.
- **왜 먼저인가** - 이게 왜 다른 줄보다 위에 있나.
- **무엇을 하면 되나** - 누구에게 무엇을 요청하나, 무엇을 확인하나.
- **근거** - 어느 과제, 어느 파일, 어느 경로인가.

여기서 그것을 만든다. 화면(`gui/action_dialog.py`)은 그리기만 한다.

## 이 도구는 지우지 않는다

할 일은 전부 **확인하고, 담당자에게 요청하는 것**이다. 확인 명령도 읽기만
하는 것(`ls`, `du`, `stat`, `df`)만 낸다. 정리 후보를 "지워도 된다" 고 부르지
않는 것도 같은 이유다 - 백업이 확인됐다는 것까지가 이 도구가 아는 사실이다.

## 근거가 아직 없어도 먼저 보인다

근거(`account_detail.AccountDetail`)는 NFS 위의 DB 몇 개를 읽어야 해서 창보다
늦게 온다. 줄에 이미 담긴 숫자만으로 상황·이유·할 일을 먼저 만들고, 근거가
오면 다시 만든다 - 사람은 기다리는 동안 이미 읽기 시작한다.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import List

from . import formatting, health, i18n, priority

# 목록 줄에 붙지 않는 안내들 (Plan 의 곁말).
KIND_UNSCANNED = "unscanned"
KIND_COARSE = "coarse"

# 근거 표와 확인 명령에 올릴 수. 이 창은 판단을 돕는 곳이지 전부를 보는 곳이
# 아니다 - 전부는 계정 상세 창과 상세 스캔 탭에 있다.
EVIDENCE_ROWS = 12
COMMANDS_SHOWN = 3

LEVEL_KEYS = {
    priority.LEVEL_CRITICAL: "guide.level.critical",
    priority.LEVEL_HIGH: "guide.level.high",
    priority.LEVEL_MEDIUM: "guide.level.medium",
}


@dataclass
class EvidenceRow:
    cells: List[str]
    # 툴팁과 복사에 쓸 전체 경로.
    path: str = ""
    # 눈에 띄게 칠할지 (위험한 줄만).
    warn: bool = False


@dataclass
class Guide:
    kind: str
    level: int
    headline: str
    situation: List[str] = field(default_factory=list)
    why: str = ""
    steps: List[str] = field(default_factory=list)
    evidence_title: str = ""
    evidence_columns: List[str] = field(default_factory=list)
    evidence_rows: List[EvidenceRow] = field(default_factory=list)
    # 읽기만 하는 확인 명령 (터미널에 붙여 넣는다).
    commands: List[str] = field(default_factory=list)
    # 판단을 흐리는 사정 (성긴 판정, 못 읽은 근거 등).
    warnings: List[str] = field(default_factory=list)
    account_id: str = ""
    account_path: str = ""
    # 복사 버튼이 줄 대표 경로.
    path: str = ""
    # 근거를 아직 못 받았다.
    loading: bool = False

    @property
    def level_text(self) -> str:
        return i18n.t(LEVEL_KEYS.get(self.level, "guide.level.medium"))


# -- 목록 한 줄 -------------------------------------------------------------
def headline(action) -> str:
    """할 일 한 줄을 사람 문장으로. 목록과 안내 창이 같은 문장을 쓴다."""

    if action.kind == priority.ACT_FULL:
        if action.shared_count > 1:
            # 여러 계정이 같은 스토리지를 쓴다. 계정마다 한 줄씩 내면 똑같은
            # 줄이 여러 개 되어 다른 종류가 목록 밖으로 밀린다.
            key = (
                "priority.item.full_shared_with_fix" if action.reclaimable_kb
                else "priority.item.full_shared"
            )
            return i18n.t(
                key,
                mount=action.mount_point or action.account,
                accounts=action.shared_count,
                pct=f"{action.pct:.0f}",
                freeable=formatting.format_kb(action.reclaimable_kb),
                count=action.count,
            )
        if action.reclaimable_kb:
            # 문제와 해법을 한 줄에. 따로 두면 사람이 두 화면을 오가며
            # 스스로 이어 붙여야 하고, 그러면 대개 안 한다.
            return i18n.t(
                "priority.item.full_with_fix",
                account=action.account,
                pct=f"{action.pct:.0f}",
                freeable=formatting.format_kb(action.reclaimable_kb),
                count=action.count,
            )
        return i18n.t(
            "priority.item.full", account=action.account, pct=f"{action.pct:.0f}"
        )
    if action.kind == priority.ACT_NO_BACKUP:
        return i18n.t(
            "priority.item.no_backup",
            account=action.account, count=action.count,
            size=formatting.format_kb(action.size_kb),
        )
    if action.kind == priority.ACT_CLEANUP:
        return i18n.t(
            "priority.item.cleanup",
            account=action.account,
            freeable=formatting.format_kb(action.reclaimable_kb),
            count=action.count,
        )
    if action.kind == priority.ACT_BIG_FILE:
        return i18n.t(
            "priority.item.big_file",
            account=action.account,
            name=action.path.rsplit("/", 1)[-1],
            size=formatting.format_kb(action.size_kb),
            pct=f"{action.pct:.0f}" if action.pct is not None else "-",
        )
    return i18n.t(
        "priority.item.surge",
        account=action.account,
        delta=formatting.format_kb_delta(action.delta_kb),
        total=formatting.format_kb(action.size_kb),
    )


# -- 안내 ---------------------------------------------------------------------
def build(action, detail=None) -> Guide:
    """목록 한 줄을 안내로 푼다. `detail` 이 없으면 줄에 담긴 숫자만으로."""

    guide = Guide(
        kind=action.kind,
        level=action.level,
        headline=headline(action),
        account_id=action.account_id,
        account_path=detail.path if detail is not None else "",
        path=action.path if action.kind == priority.ACT_BIG_FILE else "",
        loading=detail is None,
    )
    builder = {
        priority.ACT_FULL: _full,
        priority.ACT_NO_BACKUP: _no_backup,
        priority.ACT_CLEANUP: _cleanup,
        priority.ACT_BIG_FILE: _big_file,
        priority.ACT_SURGE: _surge,
    }.get(action.kind)
    if builder is not None:
        builder(guide, action, detail)
    if detail is not None and detail.failures:
        names = ", ".join(i18n.t(f"detail.part.{part}") for part in detail.failures)
        guide.warnings.append(i18n.t("guide.partial", parts=names))
    return guide


def for_unscanned(names: List[str]) -> Guide:
    """스캔이 한 번도 안 끝난 계정들 - 목록이 비어 있는 이유."""

    return Guide(
        kind=KIND_UNSCANNED,
        level=priority.LEVEL_MEDIUM,
        headline=i18n.t(
            "priority.unscanned", count=len(names), names=", ".join(names[:3])
        ),
        situation=[i18n.t("guide.unscanned.situation", names=", ".join(names))],
        why=i18n.t("guide.unscanned.why"),
        steps=[i18n.t(f"guide.unscanned.step{n}") for n in (1, 2, 3)],
    )


def for_coarse(count: int) -> Guide:
    """BACKUP 안쪽을 못 보고 판정한 과제들 - '확인됨' 이 얼마나 믿을 만한가."""

    return Guide(
        kind=KIND_COARSE,
        level=priority.LEVEL_MEDIUM,
        headline=i18n.t("priority.coarse", count=count),
        situation=[i18n.t("guide.coarse.situation", count=count)],
        why=i18n.t("guide.coarse.why"),
        steps=[i18n.t(f"guide.coarse.step{n}") for n in (1, 2, 3)],
    )


# -- 종류별 ---------------------------------------------------------------
def _q(path: str) -> str:
    """명령에 넣을 경로. 공백·특수문자가 있어도 인자 하나로 남게 한다."""

    return shlex.quote(path)


def _rel(path: str, guide: Guide) -> str:
    return formatting.relative_path(path, guide.account_path)


def _usage_facts(guide: Guide, detail) -> None:
    sample = detail.sample if detail is not None else None
    if sample is None:
        return
    guide.situation.append(
        i18n.t(
            "guide.full.numbers",
            used=formatting.format_size_pair(sample.used_kb, sample.total_kb),
            free=formatting.format_kb(sample.avail_kb),
        )
    )
    if detail.forecast is not None:
        guide.situation.append(
            i18n.t(
                "guide.full.forecast",
                forecast=formatting.format_forecast_cell(detail.forecast),
            )
        )


def _cleanup_rows(guide: Guide, detail) -> None:
    """백업이 확인된 과제들 - 정리 후보. 비울 양이 큰 것부터."""

    items = sorted(
        (detail.backed_up if detail is not None else []),
        key=lambda item: -item.reclaimable_kb,
    )
    guide.evidence_title = i18n.t("guide.evidence.cleanup")
    guide.evidence_columns = [
        i18n.t("guide.col.task"), i18n.t("guide.col.reclaim"),
        i18n.t("guide.col.backup_size"), i18n.t("guide.col.backup_at"),
    ]
    for item in items[:EVIDENCE_ROWS]:
        guide.evidence_rows.append(EvidenceRow(
            cells=[
                item.label,
                formatting.format_kb(item.reclaimable_kb),
                formatting.format_kb(item.mirror_size_kb),
                item.mirror_path or "-",
            ],
            path=item.run_path,
        ))
    for item in items[:COMMANDS_SHOWN]:
        if item.mirror_path:
            guide.commands.append(
                f"du -sh {_q(item.run_path + '/BACKUP')} {_q(item.mirror_path)}"
            )
    if any(item.coarse for item in items):
        guide.warnings.append(i18n.t("guide.coarse_warning"))


def _growth_rows(guide: Guide, detail, title_key: str) -> None:
    """많이 늘어난 경로. 비교할 이전 스캔이 없으면 큰 경로."""

    rows = list(detail.growth_rows) if detail is not None else []

    def delta(row):
        keys = row.keys() if hasattr(row, "keys") else []
        if "current_kb" in keys and row["previous_kb"] is not None:
            return row["current_kb"] - row["previous_kb"]
        return None

    rows.sort(key=lambda row: -(delta(row) if delta(row) is not None else -1))
    guide.evidence_title = i18n.t(title_key)
    guide.evidence_columns = [
        i18n.t("guide.col.path"), i18n.t("guide.col.size"), i18n.t("guide.col.delta"),
    ]
    for row in rows[:EVIDENCE_ROWS]:
        keys = row.keys() if hasattr(row, "keys") else []
        size = row["current_kb"] if "current_kb" in keys else row["size_kb"]
        change = delta(row)
        guide.evidence_rows.append(EvidenceRow(
            cells=[
                _rel(row["path"], guide),
                formatting.format_kb(size),
                formatting.format_kb_delta(change) if change is not None
                else i18n.t("common.none"),
            ],
            path=row["path"],
        ))
    for row in rows[:COMMANDS_SHOWN]:
        guide.commands.append(f"du -sh {_q(row['path'])}")


def _shared_rows(guide: Guide, action) -> None:
    """같은 스토리지를 쓰는 계정별로 정리할 것이 얼마나 있나.

    스토리지 한 줄에서 사람이 다음에 묻는 것은 "그래서 어느 계정을 손대나" 다."""

    guide.evidence_title = i18n.t("guide.evidence.shared")
    guide.evidence_columns = [
        i18n.t("guide.col.account"), i18n.t("guide.col.reclaim"),
        i18n.t("guide.col.tasks"),
    ]
    dash = i18n.t("common.none")
    for name, _account_id, freeable, count in action.shared:
        guide.evidence_rows.append(EvidenceRow(cells=[
            name,
            formatting.format_kb(freeable) if freeable else dash,
            f"{count:,}" if count else dash,
        ]))


def _full(guide: Guide, action, detail) -> None:
    if action.shared_count > 1:
        guide.situation.append(
            i18n.t(
                "guide.full.shared",
                mount=action.mount_point or "-",
                count=action.shared_count,
                names=", ".join(name for name, _id, _kb, _n in action.shared),
                pct=f"{action.pct:.1f}",
            )
        )
        # 어떻게 알았는지도 적는다 - `df` 가 같은 숫자를 돌려준다는 것이
        # 우리가 아는 전부다.
        guide.situation.append(i18n.t("guide.full.shared_how"))
    else:
        guide.situation.append(
            i18n.t("guide.full.situation", account=action.account, pct=f"{action.pct:.1f}")
        )
    _usage_facts(guide, detail)
    guide.why = i18n.t(
        "guide.full.why_now" if action.critical else "guide.full.why_soon"
    )
    if action.shared_count > 1:
        guide.why += " " + i18n.t("guide.full.why_shared")
    if action.reclaimable_kb:
        guide.steps = [
            i18n.t("guide.full.fix.step1", count=action.count,
                   freeable=formatting.format_kb(action.reclaimable_kb)),
            i18n.t("guide.full.fix.step2"),
            i18n.t("guide.full.fix.step3"),
        ]
    else:
        guide.steps = [
            i18n.t("guide.full.nofix.step1"),
            i18n.t("guide.full.nofix.step2"),
            i18n.t("guide.full.nofix.step3"),
        ]
    if action.shared_count > 1:
        # 여러 계정이 걸린 줄에서는 **어느 계정부터인지**가 먼저다. 한 계정의
        # 과제 목록은 그 계정을 연 다음에 본다.
        _shared_rows(guide, action)
        guide.steps.insert(0, i18n.t("guide.full.shared.step"))
    elif action.reclaimable_kb:
        _cleanup_rows(guide, detail)
    else:
        _growth_rows(guide, detail, "guide.evidence.where")
    if guide.account_path:
        guide.commands.insert(0, f"df -h {_q(guide.account_path)}")


def _no_backup(guide: Guide, action, detail) -> None:
    backup = (detail.backup_name if detail is not None else "") or i18n.t("common.none")
    guide.situation.append(
        i18n.t(
            "guide.no_backup.situation", account=action.account, count=action.count,
            size=formatting.format_kb(action.size_kb), backup=backup,
        )
    )
    guide.why = i18n.t("guide.no_backup.why")
    guide.steps = [i18n.t(f"guide.no_backup.step{n}") for n in (1, 2, 3, 4)]

    items = detail.at_risk if detail is not None else []
    guide.evidence_title = i18n.t("guide.evidence.no_backup")
    guide.evidence_columns = [
        i18n.t("guide.col.task"), i18n.t("guide.col.status"),
        i18n.t("guide.col.size"), i18n.t("guide.col.missing"),
    ]
    for item in items[:EVIDENCE_ROWS]:
        missing = ", ".join(item.missing_items[:4])
        if len(item.missing_items) > 4:
            missing += i18n.t("guide.more", count=len(item.missing_items) - 4)
        guide.evidence_rows.append(EvidenceRow(
            cells=[
                item.label,
                i18n.t(f"health.status.{item.status}"),
                formatting.format_kb(item.backup_size_kb or item.run_size_kb),
                missing or i18n.t("common.none"),
            ],
            path=item.run_path,
            warn=item.status == health.MISSING,
        ))
    for item in items[:COMMANDS_SHOWN]:
        guide.commands.append(f"ls -l {_q(item.run_path + '/BACKUP')}")


def _cleanup(guide: Guide, action, detail) -> None:
    guide.situation.append(
        i18n.t(
            "guide.cleanup.situation", account=action.account, count=action.count,
            freeable=formatting.format_kb(action.reclaimable_kb),
        )
    )
    _usage_facts(guide, detail)
    guide.why = i18n.t("guide.cleanup.why")
    guide.steps = [i18n.t(f"guide.cleanup.step{n}") for n in (1, 2, 3)]
    _cleanup_rows(guide, detail)


def _big_file(guide: Guide, action, detail) -> None:
    name = action.path.rsplit("/", 1)[-1]
    guide.situation.append(
        i18n.t(
            "guide.big_file.situation", name=name,
            size=formatting.format_kb(action.size_kb),
            pct=f"{action.pct:.0f}" if action.pct is not None else "-",
        )
    )
    if action.delta_kb is not None:
        guide.situation.append(
            i18n.t("guide.big_file.grew", delta=formatting.format_kb_delta(action.delta_kb))
        )
    else:
        guide.situation.append(i18n.t("guide.big_file.new"))
    guide.why = i18n.t("guide.big_file.why")
    guide.steps = [i18n.t(f"guide.big_file.step{n}") for n in (1, 2, 3)]
    guide.commands = [f"ls -lh {_q(action.path)}", f"stat {_q(action.path)}"]

    files = detail.large_files if detail is not None else []
    guide.evidence_title = i18n.t("guide.evidence.big_file")
    guide.evidence_columns = [
        i18n.t("guide.col.file"), i18n.t("guide.col.size"),
        i18n.t("guide.col.share"), i18n.t("guide.col.delta"),
    ]
    for item in files[:EVIDENCE_ROWS]:
        guide.evidence_rows.append(EvidenceRow(
            cells=[
                _rel(item.path, guide),
                formatting.format_kb(item.size_kb),
                f"{item.share_pct:.1f}%" if item.share_pct is not None
                else i18n.t("common.none"),
                formatting.format_kb_delta(item.delta_kb)
                if item.previous_kb is not None else i18n.t("large.new"),
            ],
            path=item.path,
            # 이 줄이 가리키는 그 파일.
            warn=item.path == action.path,
        ))


def _surge(guide: Guide, action, detail) -> None:
    guide.situation.append(
        i18n.t(
            "guide.surge.situation", account=action.account,
            delta=formatting.format_kb_delta(action.delta_kb),
            total=formatting.format_kb(action.size_kb),
        )
    )
    _usage_facts(guide, detail)
    guide.why = i18n.t("guide.surge.why")
    guide.steps = [i18n.t(f"guide.surge.step{n}") for n in (1, 2, 3)]
    _growth_rows(guide, detail, "guide.evidence.surge")
