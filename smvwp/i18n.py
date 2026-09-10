"""한국어/영어 UI 문자열 카탈로그.

폐쇄망이라 gettext 도구 체인(`msgfmt` 등)이나 외부 번역 라이브러리를 쓸 수
없으므로, 표준 라이브러리만으로 충분한 단순 dict 카탈로그를 쓴다. 키는 안정된
식별자이고 값은 `str.format` 템플릿이다.

설계 메모:
- 없는 키는 예외를 던지지 않고 "현재 언어 -> 기본 언어(한국어) -> 키 자체"
  순서로 되돌아간다. 번역 하나가 빠졌다고 GUI 전체가 죽으면 안 되고, 대신
  화면에 키가 그대로 보여서 빠진 것을 바로 알 수 있다.
- 언어 설정은 `config.json`의 `settings.language`에 저장하고, 프로세스 시작
  시 `set_language`로 한 번 적용한다. 전역 상태를 쓰는 이유는 등급 라벨처럼
  GUI가 아닌 계층(`tiers.py`)에서도 번역이 필요하기 때문이다.
- 파일에 기록되는 값(알림 JSON, 보고서)에는 언어 중립 코드(`tier` 등)를 항상
  함께 남긴다 - 나중에 다른 언어로 다시 렌더링할 수 있어야 하기 때문.
"""

from __future__ import annotations

from typing import Dict, List

KOREAN = "ko"
ENGLISH = "en"
DEFAULT_LANGUAGE = KOREAN

LANGUAGE_NAMES = {
    KOREAN: "한국어 (KOR)",
    ENGLISH: "English (ENG)",
}

_CATALOG: Dict[str, Dict[str, str]] = {
    KOREAN: {
        # -- 등급 --------------------------------------------------
        "tier.normal": "정상",
        "tier.warn": "주의",
        "tier.alert": "경고",
        "tier.emergency": "긴급",
        "tier.full": "가득참",
        "tier.unknown": "확인불가",
        # -- 공통 --------------------------------------------------
        "common.none": "-",
        "common.save": "저장",
        "common.cancel": "취소",
        "common.close": "닫기",
        "common.yes": "예",
        "common.no": "아니오",
        "common.unknown_value": "확인불가",
        # -- 대시보드 ----------------------------------------------
        "app.title": "Storage Manager VWP",
        "dashboard.df_caveat": (
            "※ 사용률은 계정 경로가 속한 파일시스템 전체 사용량 기준입니다 "
            "(df 특성상 계정 단독 사용량이 아닙니다)."
        ),
        "dashboard.col.name": "이름",
        "dashboard.col.path": "경로",
        "dashboard.col.size": "사용량 / 총 용량",
        "dashboard.tip.filesystem": "파일시스템: {value}",
        "dashboard.tip.mount": "마운트: {value}",
        "dashboard.tip.used": "사용량: {value}",
        "dashboard.tip.total": "총 용량: {value}",
        "dashboard.tip.free": "남은 용량: {value}",
        "dashboard.col.byte_pct": "용량 사용률",
        "dashboard.col.inode_pct": "inode 사용률",
        "dashboard.col.quota": "quota",
        "dashboard.col.tier": "종합 등급",
        "dashboard.col.collected_at": "최근 수집",
        "dashboard.col.status": "상태",
        "dashboard.col.kind": "성격",
        "dashboard.waiting": "수집 대기 중...",
        "dashboard.btn.collect_now": "새로고침",
        "dashboard.btn.collect_now_tooltip": (
            "df로 사용률을 다시 읽어 표를 갱신합니다. 즉시 끝나며 대상 파일시스템에 "
            "부하를 주지 않습니다 (디렉터리를 훑는 것은 '상세 스캔' 탭입니다)."
        ),
        "dashboard.btn.accounts": "계정 관리 / 설정...",
        "dashboard.btn.diagnose": "진단...",
        "dashboard.btn.reports": "보고서...",
        "dashboard.btn.search": "검색...",
        "dashboard.no_accounts": "등록된 계정이 없습니다. '계정 관리'에서 추가하세요.",
        "dashboard.all_normal": "모든 계정 정상 ({count}개 계정)",
        "dashboard.warn_summary": "주의 이상 계정 {count}개 - 가장 급함: {worst}",
        "dashboard.hero_detail": "가장 높은 사용률 · {account}",
        "dashboard.stat.accounts": "계정",
        "dashboard.stat.attention": "주의 이상",
        "dashboard.stat.collected": "마지막 수집",
        "dashboard.not_collected": "아직 수집되지 않음",
        "dashboard.collect_ok": "정상 수집",
        "dashboard.collect_failed": "수집 실패: {message}",
        "dashboard.collecting": "수집 중...",
        "dashboard.collected": "수집 완료 ({count}개 계정)",
        "dashboard.collected_with_failures": "수집 완료 ({count}개 계정, 실패 {failed}건)",
        "dashboard.collect_error": "수집 오류: {message}",
        # -- 수집 신선도 --------------------------------------------
        "freshness.just_now": "방금",
        "freshness.minutes_ago": "{minutes}분 전",
        "freshness.hours_ago": "{hours}시간 전",
        "freshness.days_ago": "{days}일 전",
        "freshness.never": "수집된 적 없음",
        "freshness.stale_summary": "⚠ 계정 {count}개의 수집이 멈춰 있습니다 (가장 오래된 것: {age})",
        "freshness.gappy_summary": (
            "⚠ 계정 {count}개는 최근 {hours}시간 중 {coverage}%만 수집됐습니다. "
            "cron이 돌지 않아 GUI를 열 때만 수집되고 있을 수 있습니다 "
            "(확인: crontab -l)"
        ),
        "freshness.notify_stale": (
            "[수집 지연] {account} - 마지막 수집이 {age}입니다. 수집기가 멈췄는지 확인하세요."
        ),
        # -- 메뉴 --------------------------------------------------
        "tab.home": "홈",
        "tab.scan": "상세 스캔",
        "menu.language": "언어",
        "menu.file": "파일",
        # -- 상세 스캔 ---------------------------------------------
        "scan.section_title": "상세 스캔 - 밤마다 계정 안 어디가 얼마인지",
        "scan.status_loading": "스캔 상태 확인 중...",
        "scan.btn.run_now": "지금 상세 스캔 실행",
        "scan.btn.run_now_tooltip": (
            "시간창(22:00~06:00)과 무관하게 지금 실행합니다. 대상 파일시스템에 "
            "부하를 줄 수 있으므로 업무 시간에는 주의해서 사용하세요."
        ),
        "scan.btn.stop": "안전 중지",
        "scan.btn.stop_tooltip": (
            "실행 중인 스캔에 중지를 요청합니다. 강제 종료가 아니라 다음 "
            "체크포인트에서 스스로 멈추고, 완료한 작업은 그대로 보존됩니다."
        ),
        "scan.running": "실행 중",
        "scan.not_running": "실행 중 아님",
        "scan.latest_run": "최근 실행: {status} ({started_at})",
        "scan.pending_tasks": "남은 디렉터리 작업 {count}개",
        "scan.progress_counts": (
            "디렉터리 {done:,}/{total:,} 완료 ({percent}%) · 분할되면 총계가 늘 수 있습니다"
        ),
        "scan.status_error": "스캔 상태를 읽을 수 없습니다: {message}",
        "scan.account_label": "계정",
        "scan.col.path": "경로",
        "scan.col.current_size": "현재 크기",
        "scan.col.delta": "이전 스캔 대비",
        "scan.col.delta_dated": "{previous} 대비",
        "scan.nth": "{n}번째 스캔",
        "scan.select_account": "계정을 선택하면 증가 경로가 표시됩니다.",
        "scan.no_baseline": "{account}: 아직 완료된 기준선이 없습니다 (상세 스캔이 한 바퀴 끝나야 표시됩니다).",
        "scan.growth_caption": "{account}: {current} 스캔 기준, {previous} 스캔과 같은 경로끼리 비교{activity}",
        "scan.baseline_only_caption": (
            "{account}: {current} 스캔 결과만 있습니다 "
            "(비교할 이전 스캔이 없어 증감은 다음 스캔부터 표시됩니다){activity}"
        ),
        "scan.activity_note": " · 최근 변경 파일 {count:,}개",
        "scan.partial_warning": (
            "⚠ 경로 {count}곳은 읽을 수 없는 하위 디렉터리가 있어 실제보다 작게 "
            "측정되었습니다 (권한 부족). 증가량도 그만큼 축소될 수 있습니다."
        ),
        "scan.cpu_usage": (
            "직전 스캔 CPU: 평균 {avg}% · 최대 {peak}% (top 기준, 코어 1개=100%) "
            "· 장비 전체의 {system}%"
        ),
        "scan.memory_usage": "메모리 최대 {peak} (장비 전체의 {percent}%)",
        "scan.cpu_caveat": (
            "※ 이 값은 **이 장비에서 본 CPU**입니다. 상세 스캔의 실제 부담은 대개 "
            "파일서버 쪽 I/O인데 그것은 여기서 관측할 수 없습니다."
        ),
        "scan.failed_warning": (
            "⚠ 경로 {count}곳은 크기를 재지 못했습니다. 아래 사유를 확인하세요 "
            "(권한 부족이면 관리자에게 읽기 권한을 요청하거나 대상에서 제외하면 됩니다)."
        ),
        "scan.failed_more": "  … 외 {count}곳 (전체 목록은 주간 보고서에서)",
        "reports.scan_progress_heading": "[상세 스캔 진행 상황]",
        # -- 상세 스캔 탭: 계정별 현황 표 --------------------------
        "scan.acct.heading": "계정별 현황  ·  행을 누르면 아래 증가 경로가 그 계정으로 바뀝니다",
        "scan.acct.name": "계정",
        "scan.acct.kind": "성격",
        "scan.acct.progress": "진행",
        "scan.acct.pending": "남은 작업",
        "scan.acct.measured": "지금까지 찾은 용량",
        "scan.acct.eta": "예상 남은 시간",
        "scan.acct.last_scan": "최근 스캔",
        "scan.acct.note": "비고",
        "scan.acct.never": "아직 없음",
        "scan.acct.progress_tip": (
            "완료한 디렉터리 / 전체. 전체는 진행 중 늘어날 수 있습니다 - "
            "시간이 오래 걸리는 디렉터리를 쪼개면 작업이 추가되기 때문입니다. "
            "진행률이 가끔 뒤로 가는 것은 고장이 아닙니다."
        ),
        "scan.acct.measured_tip": (
            "이번 스캔에서 지금까지 실제로 측정한 용량입니다. 스캔이 도는 동안 "
            "계속 늘어납니다. 계정 전체 용량이 아니라 '여기까지 세어 본 양'입니다."
        ),
        "scan.acct.eta_value": "약 {duration}",
        "scan.acct.eta_unknown": "측정 중",
        "scan.acct.eta_tip": (
            "이번 스캔이 실제로 낸 속도(디렉터리 하나당 걸린 시간의 중앙값)에 "
            "남은 개수를 곱한 어림값입니다. 미리 계산한 예측이 아닙니다.\n\n"
            "남은 디렉터리가 지나온 것보다 무거우면 더 걸리고, 쪼개기로 작업이 "
            "늘면 값이 커집니다. 표본이 모자라면 '측정 중'으로 남습니다."
        ),
        "scan.acct.note_failed": "실패 {count}곳",
        "scan.acct.note_partial": "일부만 읽음 {count}곳",
        "scan.acct.note_changed": "변경 파일 {count:,}개",
        "reports.large_heading": "[눈에 띄는 큰 파일]",
        "reports.large_caveat": (
            "파일 하나가 계정의 5% 이상을 차지하거나, 지난 스캔보다 1.5배 이상 "
            "커졌거나, 지난 스캔 상위 목록에 없던 것만 싣습니다. "
            "'목록에 없던 파일'은 새로 생겼다는 뜻이 아닙니다 - 그때는 작았을 "
            "수도 있습니다."
        ),
        "large.heading": "가장 큰 파일  ·  파일 하나가 유난히 크면 여기서 보입니다",
        "large.col.path": "파일",
        "large.col.size": "크기",
        "large.col.share": "계정 비중",
        "large.col.change": "지난 스캔 대비",
        "large.none": "{account}: 100MB 이상인 파일이 아직 없습니다.",
        "large.no_scan": "계정을 선택하면 가장 큰 파일이 표시됩니다.",
        "large.new": "목록에 없던 파일",
        "large.reason.share": "계정의 {pct}%를 혼자 차지",
        "large.reason.grew": "지난 스캔보다 {ratio}배",
        "large.reason.new": "지난 스캔 상위 목록에 없었음",
        "large.tip": (
            "순회가 파일을 훑는 김에 100MB 이상인 것 중 큰 순서로 모아 둡니다.\n\n"
            "'목록에 없던 파일'은 새로 생겼다는 뜻이 아닙니다 - 지난 스캔의 상위 "
            "목록에 없었다는 것까지만 알 수 있습니다(그때는 작았을 수도 있습니다)."
        ),
        "scan.acct.changed_tip": (
            "지난 스캔 이후 수정 시각이 바뀐 파일의 **개수**입니다. 어떤 파일이 "
            "어떻게 바뀌었는지는 담지 않습니다 - '이 계정이 활발하다'까지만 "
            "말해 줍니다. 5,000개가 넘으면 그 이상은 세지 않습니다."
        ),
        # -- 기간 표기 ---------------------------------------------
        "duration.under_minute": "1분 미만",
        "duration.minutes": "{minutes}분",
        "duration.hours": "{hours}시간",
        "duration.hours_minutes": "{hours}시간 {minutes}분",
        "duration.days_hours": "{days}일 {hours}시간",
        # -- 과제 생성 (의뢰서 기반 워크플로) ----------------------
        "reports.new_tasks_heading": "[과제 생성]",
        "reports.new_tasks_none": "직전 스캔 이후 새로 생긴 과제가 없습니다.",
        "reports.new_tasks_no_project_accounts": (
            "성격이 '프로젝트'인 계정이 없어 과제 생성을 보지 않았습니다 "
            "(계정 관리 화면에서 성격을 지정하면 다음 보고서부터 나옵니다)."
        ),
        "reports.new_tasks_count": "새로 생긴 과제 {count}건",
        "reports.new_task_stages": "단계: {stages}",
        "reports.new_tasks_truncated": "  … 계정당 {shown}건까지만 표시했습니다",
        # -- 스캔 중 리소스 변화 ------------------------------------
        "reports.company_heading": "[그때 서버에서 무엇이 돌았나]",
        "reports.company_context": (
            "우리 스캔을 뺀 나머지가 쓴 CPU: 평균 {cpu}, 최고 {peak} · "
            "I/O 를 기다린 남의 작업 평균 {blocked}개"
        ),
        "reports.company_col.user": "사용자",
        "reports.company_col.job": "작업",
        "reports.company_col.cpu_peak": "CPU최고",
        "reports.company_col.mem_peak": "메모리최고",
        "reports.company_alone": "이 시간에 서버를 쓴 다른 작업이 잡히지 않았습니다.",
        "reports.company_mount": (
            "{mount} - {ops}건, 왕복 {rtt}ms, 대기 {queue}ms"
        ),
        "reports.company_mount_note": (
            "  대기가 왕복보다 크면 병목은 파일서버가 아니라 이쪽 RPC 슬롯입니다."
        ),
        "reports.resource_heading": "[스캔 중 리소스 변화]",
        "reports.resource_run": "{started} · {trigger} · {duration} · {status}",
        "reports.resource_other_runs": "같은 기간의 다른 실행 (이것도 부하를 만들었습니다):",
        "reports.trigger.cron": "자동(cron)",
        "reports.trigger.gui": "화면에서 수동 실행",
        "reports.trigger.terminal": "터미널에서 수동 실행",
        "reports.duration_minutes": "{minutes}분",
        "reports.duration_hours": "{hours}시간 {minutes}분",
        # -- 평일 밤 vs 주말 밤 부하 비교 ---------------------------
        "reports.night_heading": "[야간 부하 비교 - 평일 밤 vs 주말 밤]",
        "reports.night_intro": (
            "최근 {days}일. '증가폭'은 그날 스캔 직전 값 대비 최고치라, 밤마다 "
            "평소 부하가 달라도 서로 비교됩니다. 밤의 구분은 **끝나는 아침** "
            "기준입니다 - 금요일 밤은 주말, 일요일 밤은 평일입니다."
        ),
        "reports.night_weekday": "평일 밤",
        "reports.night_weekend": "주말 밤",
        "reports.night_col.kind": "구분",
        "reports.night_col.count": "밤 수",
        "reports.night_col.parallel": "동시볼륨",
        "reports.night_col.load_delta": "load 증가폭",
        "reports.night_col.iowait_delta": "I/O대기 증가폭",
        "reports.night_col.load_peak": "load 최고",
        "reports.night_col.iowait_peak": "I/O대기 최고",
        "reports.night_col.duration": "스캔 시간",
        "reports.night_col.date": "날짜",
        "reports.night_col.status": "상태",
        "reports.night_hours": "{hours}시간",
        "reports.night_caveat": (
            "값은 밤마다의 중앙값입니다 (한 밤이 유난히 무거워도 끌려가지 않게). "
            "주말 밤의 증가폭이 크더라도 '스캔 시간'이 함께 줄었다면 같은 일을 "
            "짧게 끝낸 것이고, 시간이 안 줄었는데 부하만 늘었다면 이 장비에서는 "
            "병렬이 이득이 아닙니다 - 그때는 weekend_parallel_accounts를 내리세요."
        ),
        "reports.night_detail_heading": "밤별 상세",
        "reports.night_detail_more": "  … 외 {count}개 밤",
        "reports.resource_context": (
            "표본 {samples}개 · 동시 실행 볼륨 {parallel}개 기준"
        ),
        "reports.resource_caveat": (
            "여기 숫자는 이 서버에서 본 것뿐입니다. du/find의 실제 부담은 대개 "
            "파일서버 쪽 I/O인데 그것은 이 프로세스에서 관측할 수 없습니다. "
            "CPU가 낮아도 iowait과 load가 올랐다면 부하가 없었던 것이 아닙니다."
        ),
        "reports.resource_timeline_heading": "시간 흐름 (스캔 시작 이후 경과)",
        "reports.resource_col.metric": "지표",
        "reports.resource_col.before": "스캔 전",
        "reports.resource_col.average": "스캔 중 평균",
        "reports.resource_col.peak": "최고",
        "reports.resource_col.delta": "증가폭",
        "reports.resource_col.elapsed": "경과",
        "reports.resource_col.accounts": "동시계정",
        "reports.resource_metric.load_avg": "load(1분)",
        "reports.resource_metric.cpu_iowait": "I/O대기",
        "reports.resource_metric.cpu_busy": "CPU",
        "reports.resource_metric.memory_used": "메모리",
        "reports.resource_metric.scan_share": "스캔몫",
        "reports.scan_compared_with": "기준: {previous} 스캔 데이터 대비",
        "reports.scan_heading": "{account} ({run} 스캔)",
        "reports.scan_progress": (
            "진행: 디렉터리 {done:,}/{total:,} 완료 ({percent}%) · 대기 {pending:,}"
        ),
        "reports.scan_last_path": "마지막 처리: {when} · {size}",
        "reports.scan_failed_header": "[!] 크기를 재지 못한 경로 {count}곳:",
        "scan.close_while_running_title": "상세 스캔 실행 중",
        "scan.close_while_running_body": (
            "상세 스캔이 돌고 있습니다. 창을 닫으면 스캔도 함께 멈춥니다.\n\n"
            "지금까지 끝낸 디렉터리 결과는 남고, 진행 중이던 디렉터리 하나만 "
            "다음 스캔에서 다시 처리합니다.\n\n닫을까요?"
        ),
        "scan.stopped_on_close": "실행 중이던 작업 {count}개를 정리했습니다.",
        "scan.btn.progress": "진행 상황 보기...",
        "scan.btn.progress_tooltip": (
            "선택한 계정이 어디까지 훑었는지 경로 단위로 봅니다 (읽기 전용)."
        ),
        "scan.current_target": "지금: {account} · {kind} · {path}",
        "scan.scanning_now": "상세 스캔 진행 중 · {path}",
        "progress.title": "상세 스캔 진행 상황",
        "progress.kind_label": "무엇을",
        "progress.kind.baseline": "용량 - 디렉터리마다 얼마인지",
        "progress.kind.activity": "변경 - 지난 스캔 이후 바뀐 파일 수",
        "progress.btn.refresh": "새로고침",
        "progress.summary": (
            "{generation} 스캔 · 완료 {done} / 대기 {pending} / 분할 {split} / "
            "실패 {error} (전체 {total})"
        ),
        "progress.col.path": "경로",
        "progress.col.status": "상태",
        "progress.col.result": "결과",
        "progress.col.scanned_at": "처리 시각",
        "progress.status.pending": "대기",
        "progress.status.done": "완료",
        "progress.status.split": "분할됨",
        "progress.status.error": "실패",
        "progress.changed": "변경 {count:,}개",
        "notify.urgent_prefix": "즉시 확인",
        "scan.new_path": "신규 (이전 스캔에 없음)",
        "scan.no_change": "변화 없음",
        "scan.confirm_title": "상세 스캔 실행",
        "scan.confirm_body": (
            "야간 시간창과 무관하게 지금 상세 스캔을 실행합니다.\n"
            "du/find가 대상 파일시스템을 훑으므로 부하가 생길 수 있습니다.\n\n"
            "계속할까요?"
        ),
        "scan.already_running": "상세 스캔이 이미 실행 중입니다.",
        "scan.started": "상세 스캔 실행 중...",
        "scan.auto_started": "야간 시간창에 들어와 상세 스캔을 시작했습니다.",
        "cron.ok": "cron: 수집·야간 스캔 모두 등록되어 있습니다.",
        "dashboard.forecast_failed": "FULL 예측을 계산하지 못했습니다 (표본은 정상입니다).",
        "cron.none": (
            "cron에 등록된 항목이 없습니다 - 이 창을 닫으면 수집도 야간 스캔도 "
            "돌지 않습니다. setup_cron.csh 를 한 번 실행하세요."
        ),
        "cron.nightly_missing": (
            "cron에 야간 스캔이 없습니다 - 이 창을 닫아 두면 밤에 아무것도 "
            "돌지 않습니다. setup_cron.csh 를 실행하거나, 설정에서 "
            "'야간 자동 스캔'을 켜세요."
        ),
        "cron.collector_missing": (
            "cron에 15분 수집이 없습니다 - 이 창을 닫은 동안 사용량 이력이 "
            "끊겨 예측이 어긋납니다."
        ),
        "cron.nightly_by_gui": (
            "야간 스캔은 이 창이 맡습니다 (cron 미등록). 창을 닫으면 그날 밤은 "
            "돌지 않습니다."
        ),
        "cron.unknown": "cron 등록 여부를 확인하지 못했습니다.",
        "scan.stop_requested": "중지를 요청했습니다. 다음 체크포인트에서 안전하게 멈춥니다.",
        "scan.nothing_running": "실행 중인 상세 스캔이 없습니다.",
        "scan.not_started": "상세 스캔 미실행: {reason}",
        "scan.finished": "상세 스캔 종료 (상태: {status})",
        "scan.failed": "상세 스캔 오류: {message}",
        "scan.window_open": "야간 시간창 진행 중 (종료까지 약 {minutes}분)",
        "scan.window_closed": "야간 시간창 아님 (시작 {start:02d}:00 ~ 종료 {end:02d}:00)",
        # -- 계정 다이얼로그 ---------------------------------------
        "accounts.title": "계정 관리 / 설정",
        "accounts.registered": "등록된 계정",
        "accounts.col.name": "이름",
        "accounts.col.path": "경로",
        "accounts.col.owner": "추가한 사람",
        "accounts.col.added": "추가일",
        "accounts.col.scanned": "최근 스캔일",
        "accounts.col.kind": "성격",
        "accounts.parallel_weekday": "야간 동시 스캔 (평일)",
        "accounts.parallel_weekend": "야간 동시 스캔 (주말)",
        "accounts.parallel_hint": (
            "서로 다른 저장소(볼륨)를 한 번에 몇 개까지 동시에 스캔할지. "
            "1이면 지금까지처럼 하나씩 돕니다. 같은 볼륨에 있는 계정들은 이 "
            "값을 올려도 자동으로 하나씩 돕니다 - 같은 볼륨을 여럿이 두들기면 "
            "서로를 방해할 뿐 빨라지지 않는다는 것이 실측으로 확인됐습니다. "
            "그래서 계정이 전부 한 볼륨에 있으면 값을 올려도 달라지지 않습니다. "
            "주말 밤은 '끝나는 아침이 토/일인 밤'입니다 - 금요일 밤은 주말, "
            "일요일 밤은 평일입니다 (월요일 아침에 전원이 출근하므로)."
        ),
        "accounts.suffix.accounts": "개",
        "accounts.auto_scan": "야간 자동 스캔 (이 창이 켜져 있을 때)",
        "accounts.auto_scan_hint": (
            "이 창이 켜져 있으면 야간 시간창(기본 22시)에 상세 스캔을 스스로 "
            "시작합니다. 밤마다 한 번만 시작하고, **창을 닫으면 함께 멈춥니다**.\n\n"
            "대신 그날 아무도 창을 켜 두지 않으면 그 밤은 통째로 빕니다. "
            "사람이 없어도 도는 쪽을 원하시면 이 항목을 끄고 cron에 "
            "등록하세요 (setup_cron.csh).\n\n"
            "둘 다 켜 두어도 안전합니다 - 한 번에 하나만 돌도록 잠금이 막습니다."
        ),
        "accounts.engine": "측정 방법",
        "accounts.engine.python": "파이썬 순회 (빠름)",
        "accounts.engine.du": "du 실행 (예전 방식)",
        "accounts.engine_hint": (
            "상세 스캔에서 용량을 무엇으로 재는지. 파이썬 순회는 파일서버에 "
            "여러 요청을 동시에 띄워 왕복 지연을 감춥니다 - 반입 장비 실측에서 "
            "같은 계정이 du 75~88초 대 순회 36초였고, 합계는 정확히 "
            "일치했습니다.\n\n"
            "du 방식은 별도 프로세스라 nice/ionice로 우선순위를 낮출 수 "
            "있습니다. 순회는 이 프로그램 안에서 돌아 그 설정이 듣지 않으니, "
            "낮추고 싶으면 cron 항목을 'nice -n 10 ...'으로 거세요.\n\n"
            "밤에 문제가 생기면 여기서 du로 되돌리면 됩니다 - 다른 설정은 "
            "그대로 두어도 됩니다."
        ),
        "accounts.col.backup_link": "연결 백업 계정",
        "accounts.kind": "계정 성격",
        "accounts.kind_hint": (
            "프로젝트 계정에서만 과제 생성(*_run_*)을 봅니다. "
            "백업 계정은 단조 증가가 정상이라 같은 눈으로 보면 안 됩니다."
        ),
        "accounts.backup_link_none": "(연결 없음)",
        "accounts.backup_link_needs_backup_account": (
            "연결할 백업 계정이 없습니다. 먼저 성격이 '백업'인 계정을 등록하세요."
        ),
        "account.kind.unset": "미지정",
        "account.kind.project": "프로젝트",
        "account.kind.backup": "데이터 백업",
        "accounts.advanced_settings": "상세 설정",
        "accounts.name_placeholder": "계정 이름 (예: project_a)",
        "accounts.path_placeholder": "모니터링 대상 경로 (예: /user/project_a)",
        "accounts.btn.browse": "경로 찾기...",
        "accounts.btn.add": "계정 추가",
        "accounts.btn.remove": "선택한 계정 삭제",
        "accounts.browse_title": "모니터링 대상 디렉터리 선택",
        "accounts.input_required_title": "입력 필요",
        "accounts.input_required_body": "계정 이름과 경로를 모두 입력하세요.",
        "accounts.add_failed": "계정 추가 실패",
        "accounts.remove_title": "계정 삭제",
        "accounts.remove_body": "'{name}' 계정을 목록에서 삭제할까요? (수집 이력은 남아 있습니다)",
        "accounts.save_failed": "저장 실패",
        "accounts.global_settings": "전역 설정",
        "accounts.interval": "수집 주기",
        "accounts.cooldown": "알림 재발송 대기(cooldown)",
        "accounts.retention": "표본 보존 기간",
        "accounts.language": "표시 언어",
        "accounts.notification_mode": "알림 방식",
        "accounts.notification_command": "알림 command (JSON 배열)",
        "accounts.notification_webhook": "알림 webhook 주소",
        "accounts.quota_command": "quota command (JSON 배열)",
        "accounts.suffix.minutes": " 분",
        "accounts.suffix.days": " 일",
        "accounts.none_selected_title": "계정 없음",
        "accounts.none_selected_body": "먼저 계정을 등록하세요.",
        # -- 읽기 가능 범위 -----------------------------------------
        "readability.title": "읽기 권한 확인",
        "readability.all_ok": "확인한 디렉터리 {checked}곳을 모두 읽을 수 있습니다.",
        "readability.all_ok_partial": (
            "확인한 디렉터리 {checked}곳은 모두 읽을 수 있습니다 (일부만 표본 확인)."
        ),
        "readability.some_unreadable": (
            "확인한 디렉터리 {checked}곳 중 {unreadable}곳을 읽을 수 없습니다. "
            "그 하위는 용량 측정에서 빠지므로 크기가 실제보다 작게 나옵니다."
        ),
        "readability.truncated_note": "(경로가 커서 일부만 표본 확인했습니다.)",
        "readability.more": "... 외 {count}곳",
        "readability.root_unreadable": "이 경로 자체를 읽을 수 없습니다.",
        "readability.register_anyway": (
            "그래도 등록할까요? df 기반 사용률과 알림은 정상 동작하며, "
            "상세 스캔의 크기만 하한선으로 보시면 됩니다."
        ),
        # -- 알림 --------------------------------------------------
        "notify.mode.outbox": "파일 outbox",
        "notify.mode.command": "사내 command (stdin)",
        "notify.mode.webhook": "내부 webhook",
        "notify.mode.disabled": "사용 안 함",
        "notify.message": "[{tier}] {account} ({path}) - 용량 {byte_pct} / inode {inode_pct}",
        "notify.growth_message": (
            "[경로 급증] {account} - {path} 이(가) 지난 스캔보다 {delta} 늘었습니다 (현재 {current})"
        ),
        "notify.full_forecast_message": (
            "[FULL 임박] {filesystem} - 약 {hours}시간 후 가득 참 예상 "
            "(대상 계정: {accounts})"
        ),
        "notify.surge_message": (
            "[계정 급증] {filesystem} - 최근 {window}시간 동안 {delta} 늘었습니다 "
            "(대상 계정: {accounts})"
        ),
        # -- FULL 예측 표시 ----------------------------------------
        "forecast.column": "FULL 예상",
        "forecast.unavailable": "예측 불가",
        "forecast.hours": "약 {hours}시간",
        "forecast.days": "약 {days}일",
        "forecast.within_hour": "1시간 이내",
        "forecast.pair": "{short} / {long}",
        "forecast.tooltip": (
            "7일 추세: {short}\n30일 추세: {long}\n"
            "최근 {window}시간 기울기: {slope}\n"
            "※ 파일시스템 전체 사용량 기준이며 추정치입니다."
        ),
        "forecast.reason.insufficient_samples": "표본 부족",
        "forecast.reason.not_growing": "증가 추세 아님",
        "forecast.reason.too_far": "예측 범위 초과",
        # -- 최초 실행 안내 ----------------------------------------
        "firstrun.title": "시작하기",
        "firstrun.heading": "Storage Manager VWP를 시작합니다",
        "firstrun.body": (
            "아직 등록된 계정이 없습니다. 아래 진단 결과를 확인한 뒤 모니터링할 "
            "계정 경로를 등록하세요.\n\n"
            "수집 데이터는 모니터링 대상과 분리된 다음 경로에 저장됩니다:\n{path}\n\n"
            "모니터링 대상 경로에는 절대 쓰거나 삭제하지 않습니다."
        ),
        "firstrun.add_account": "계정 등록하기",
        "firstrun.later": "나중에",
        # -- 진단 --------------------------------------------------
        "diagnostics.title": "진단 결과",
        # -- 보고서 ------------------------------------------------
        "reports.title": "보고서",
        "reports.daily": "일간 보고서",
        "reports.weekly": "주간 보고서",
        "reports.cleanup": "정리 후보",
        "reports.generate": "지금 생성",
        "reports.generated": "보고서를 생성했습니다: {path}",
        "reports.none": "아직 생성된 보고서가 없습니다.",
        # -- 검색 --------------------------------------------------
        "search.title": "검색 (관리자)",
        "search.pin_title": "관리자 확인",
        "search.pin_prompt": "관리자 PIN을 입력하세요:",
        "search.pin_wrong": "PIN이 올바르지 않습니다.",
        "search.pin_caveat": (
            "※ PIN은 운영체제 권한이나 암호화가 아니라 화면 노출을 제한하는 "
            "장치입니다."
        ),
        "search.query_placeholder": "파일/디렉터리 이름",
        "search.btn.run": "검색",
        "search.mode.exact": "정확히 일치",
        "search.mode.prefix": "접두 일치",
        "search.mode.contains": "포함",
        "search.enable_indexing": "이 계정 검색 인덱싱 켜기",
        "search.not_indexed": "이 계정은 검색 인덱싱이 꺼져 있습니다.",
        "search.not_indexed_hint": (
            "이 계정은 검색 인덱싱이 꺼져 있습니다. 위 '검색 인덱싱 사용'을 켜면 "
            "인덱싱을 시작합니다 (파일 이름·확장자·경로만 저장하며 내용은 저장하지 않습니다)."
        ),
        "search.no_account": "검색할 계정이 없습니다. '계정 관리'에서 먼저 등록하세요.",
        "search.empty_query": "검색어를 입력한 뒤 Enter를 누르거나 '검색'을 누르세요.",
        "search.searching": "검색 중...",
        "search.no_results": "'{query}'와 일치하는 항목이 없습니다 (인덱스 {indexed:,}건 중). 검색 방식을 '부분 일치'로 바꿔 보세요.",
        "search.index_empty": "인덱스가 비어 있습니다. 인덱싱이 끝난 뒤 다시 검색하세요.",
        "search.indexing_started": "{account} 인덱싱을 시작했습니다. 끝나면 알려 드립니다.",
        "search.indexing_in_progress": "인덱싱이 진행 중입니다. 끝난 뒤 검색하세요 (지금 결과는 일부일 수 있습니다).",
        "search.index_done": "인덱싱 완료: {count:,}건",
        "search.index_failed": "인덱싱 실패: {message}",
        "search.col.path": "상대 경로",
        "search.col.kind": "종류",
        "search.result_count": "결과 {count}건 (최대 {limit}건까지 표시)",
        "search.db_size": "검색 DB 실제 크기: {size}",
        "search.change_pin": "PIN 변경...",
        "search.pin_default_warning": "기본 PIN을 그대로 쓰고 있습니다. 변경을 권장합니다.",
        "pin.change_title": "관리자 PIN 변경",
        "pin.current": "현재 PIN",
        "pin.new": "새 PIN",
        "pin.confirm": "새 PIN 확인",
        "pin.mismatch": "새 PIN이 서로 일치하지 않습니다.",
        "pin.too_short": "PIN은 최소 {min_length}자리여야 합니다.",
        "pin.current_wrong": "현재 PIN이 올바르지 않습니다.",
        "pin.changed": "PIN을 변경했습니다.",
    },
    ENGLISH: {
        # -- Tiers -------------------------------------------------
        "tier.normal": "Normal",
        "tier.warn": "Warning",
        "tier.alert": "Alert",
        "tier.emergency": "Emergency",
        "tier.full": "Full",
        "tier.unknown": "Unknown",
        # -- Common ------------------------------------------------
        "common.none": "-",
        "common.save": "Save",
        "common.cancel": "Cancel",
        "common.close": "Close",
        "common.yes": "Yes",
        "common.no": "No",
        "common.unknown_value": "Unknown",
        # -- Dashboard ---------------------------------------------
        "app.title": "Storage Manager VWP",
        "dashboard.df_caveat": (
            "* Usage reflects the whole filesystem containing the account path "
            "(df does not report per-account usage)."
        ),
        "dashboard.col.name": "Name",
        "dashboard.col.path": "Path",
        "dashboard.col.size": "Used / Total",
        "dashboard.tip.filesystem": "Filesystem: {value}",
        "dashboard.tip.mount": "Mount: {value}",
        "dashboard.tip.used": "Used: {value}",
        "dashboard.tip.total": "Total: {value}",
        "dashboard.tip.free": "Free: {value}",
        "dashboard.col.byte_pct": "Capacity used",
        "dashboard.col.inode_pct": "Inode used",
        "dashboard.col.quota": "Quota",
        "dashboard.col.tier": "Overall tier",
        "dashboard.col.collected_at": "Last collected",
        "dashboard.col.status": "Status",
        "dashboard.col.kind": "Kind",
        "dashboard.waiting": "Waiting for first collection...",
        "dashboard.btn.collect_now": "Refresh",
        "dashboard.btn.collect_now_tooltip": (
            "Re-reads usage with df and updates the table. Finishes immediately and puts "
            "no load on the monitored filesystem (directory walks are the 'Detail scan' tab)."
        ),
        "dashboard.btn.accounts": "Accounts / Settings...",
        "dashboard.btn.diagnose": "Diagnostics...",
        "dashboard.btn.reports": "Reports...",
        "dashboard.btn.search": "Search...",
        "dashboard.no_accounts": "No accounts registered. Add one from 'Accounts / Settings'.",
        "dashboard.all_normal": "All accounts normal ({count} accounts)",
        "dashboard.warn_summary": "{count} account(s) at warning or worse - most urgent: {worst}",
        "dashboard.hero_detail": "Highest usage · {account}",
        "dashboard.stat.accounts": "Accounts",
        "dashboard.stat.attention": "Warning+",
        "dashboard.stat.collected": "Last collected",
        "dashboard.not_collected": "Not collected yet",
        "dashboard.collect_ok": "Collected",
        "dashboard.collect_failed": "Collection failed: {message}",
        "dashboard.collecting": "Collecting...",
        "dashboard.collected": "Collection done ({count} accounts)",
        "dashboard.collected_with_failures": "Collection done ({count} accounts, {failed} failed)",
        "dashboard.collect_error": "Collection error: {message}",
        # -- Collection freshness ----------------------------------
        "freshness.just_now": "just now",
        "freshness.minutes_ago": "{minutes} min ago",
        "freshness.hours_ago": "{hours} h ago",
        "freshness.days_ago": "{days} d ago",
        "freshness.never": "never collected",
        "freshness.stale_summary": "! Collection stopped for {count} account(s) (oldest: {age})",
        "freshness.gappy_summary": (
            "! {count} account(s) collected only {coverage}% of expected samples in the "
            "last {hours}h. cron may not be running, so data is only collected when the "
            "GUI is open (check: crontab -l)"
        ),
        "freshness.notify_stale": (
            "[Collection stalled] {account} - last collected {age}. Check whether the "
            "collector is still running."
        ),
        # -- Menu --------------------------------------------------
        "tab.home": "Home",
        "tab.scan": "Detail scan",
        "menu.language": "Language",
        "menu.file": "File",
        # -- Detail scan -------------------------------------------
        "scan.section_title": "Detail scan - what is where inside each account, night by night",
        "scan.status_loading": "Checking scan status...",
        "scan.btn.run_now": "Run detail scan now",
        "scan.btn.run_now_tooltip": (
            "Runs immediately regardless of the 22:00-06:00 window. This can load "
            "the target filesystem, so use it carefully during business hours."
        ),
        "scan.btn.stop": "Safe stop",
        "scan.btn.stop_tooltip": (
            "Requests a stop for the running scan. This is not a forced kill - the "
            "scan stops at its next checkpoint and completed work is preserved."
        ),
        "scan.running": "Running",
        "scan.not_running": "Not running",
        "scan.latest_run": "Last run: {status} ({started_at})",
        "scan.pending_tasks": "{count} directory task(s) remaining",
        "scan.progress_counts": (
            "{done:,}/{total:,} directories done ({percent}%) - the total can grow when directories are split"
        ),
        "scan.status_error": "Cannot read scan status: {message}",
        "scan.account_label": "Account",
        "scan.col.path": "Path",
        "scan.col.current_size": "Current size",
        "scan.col.delta": "vs previous scan",
        "scan.col.delta_dated": "vs {previous}",
        "scan.nth": "scan #{n}",
        "scan.select_account": "Select an account to see its growth paths.",
        "scan.no_baseline": "{account}: no completed baseline yet (one full detail scan is required).",
        "scan.growth_caption": "{account}: {current} scan, compared path-by-path with the {previous} scan{activity}",
        "scan.baseline_only_caption": (
            "{account}: only the {current} scan exists so far "
            "(no previous scan to compare, deltas appear after the next scan){activity}"
        ),
        "scan.activity_note": " - {count:,} changed file(s) recently",
        "scan.partial_warning": (
            "! {count} path(s) contain unreadable subdirectories, so their sizes are "
            "under-measured (insufficient permissions). Growth figures may be understated."
        ),
        "scan.cpu_usage": (
            "Last scan CPU: {avg}% avg / {peak}% peak (top scale, 1 core = 100%) "
            "- {system}% of the whole machine"
        ),
        "scan.memory_usage": "Memory peak {peak} ({percent}% of the machine)",
        "scan.cpu_caveat": (
            "Note: this is CPU **as seen on this client**. The real cost of a detail scan is "
            "usually file-server I/O, which cannot be observed from here."
        ),
        "scan.failed_warning": (
            "! {count} path(s) could not be measured. Check the reasons below "
            "(if it is a permission problem, request read access or drop the path)."
        ),
        "scan.failed_more": "  ... and {count} more (see the weekly report for the full list)",
        "reports.scan_progress_heading": "[Detail scan progress]",
        # -- Detail scan tab: per-account status --------------------
        "scan.acct.heading": "Accounts  ·  click a row to switch the growth list below",
        "scan.acct.name": "Account",
        "scan.acct.kind": "Kind",
        "scan.acct.progress": "Progress",
        "scan.acct.pending": "Pending",
        "scan.acct.measured": "Measured so far",
        "scan.acct.eta": "Est. remaining",
        "scan.acct.last_scan": "Last scan",
        "scan.acct.note": "Notes",
        "scan.acct.never": "never",
        "scan.acct.progress_tip": (
            "Directories done / total. The total can GROW while the scan runs - "
            "slow directories get split into more work. Progress moving backwards "
            "is not a bug."
        ),
        "scan.acct.measured_tip": (
            "How much this scan has actually measured so far. It keeps growing "
            "while the scan runs. This is not the account total - it is what has "
            "been counted up to now."
        ),
        "scan.acct.eta_value": "~{duration}",
        "scan.acct.eta_unknown": "measuring",
        "scan.acct.eta_tip": (
            "A rough figure: the median time this scan actually took per directory, "
            "multiplied by the number left. It is not a precomputed prediction.\n\n"
            "It grows if the remaining directories are heavier than the ones already "
            "done, or if splitting adds work. With too few samples it stays "
            "'measuring'."
        ),
        "scan.acct.note_failed": "{count} failed",
        "scan.acct.note_partial": "{count} partially read",
        "scan.acct.note_changed": "{count:,} changed files",
        "reports.large_heading": "[Files worth a look]",
        "reports.large_caveat": (
            "Listed only when a single file takes 5% or more of the account, "
            "grew 1.5x or more since the last scan, or was absent from the "
            "previous scan's top list. Absent does not mean new - it may have "
            "been smaller then."
        ),
        "large.heading": "Largest files  ·  a single oversized file shows up here",
        "large.col.path": "File",
        "large.col.size": "Size",
        "large.col.share": "Share of account",
        "large.col.change": "vs last scan",
        "large.none": "{account}: no file above 100MB yet.",
        "large.no_scan": "Pick an account to see its largest files.",
        "large.new": "not in the previous list",
        "large.reason.share": "takes {pct}% of the account on its own",
        "large.reason.grew": "{ratio}x the previous scan",
        "large.reason.new": "was not in the previous scan's top list",
        "large.tip": (
            "Collected while the walk stats files anyway - the biggest ones "
            "above 100MB.\n\n"
            "'Not in the previous list' does not mean the file is new. All we "
            "know is that it was absent from the previous scan's top list; it "
            "may simply have been smaller then."
        ),
        "scan.acct.changed_tip": (
            "The **number** of files whose modification time changed since the "
            "last scan. It does not say which files or how - only that the "
            "account is active. Counting stops at 5,000."
        ),
        # -- Durations ---------------------------------------------
        "duration.under_minute": "under a minute",
        "duration.minutes": "{minutes} min",
        "duration.hours": "{hours} h",
        "duration.hours_minutes": "{hours} h {minutes} min",
        "duration.days_hours": "{days} d {hours} h",
        # -- New tasks (request-driven workflow) --------------------
        "reports.new_tasks_heading": "[New tasks]",
        "reports.new_tasks_none": "No new tasks since the previous scan.",
        "reports.new_tasks_no_project_accounts": (
            "No account is marked as a project account, so new tasks were not "
            "checked (set the account kind in Account management and it will "
            "appear from the next report)."
        ),
        "reports.new_tasks_count": "{count} new task(s)",
        "reports.new_task_stages": "Stages: {stages}",
        "reports.new_tasks_truncated": "  ... showing at most {shown} per account",
        # -- Resource change during the scan ------------------------
        "reports.company_heading": "[What else the server was doing]",
        "reports.company_context": (
            "CPU used by everything except our scan: {cpu} average, {peak} peak - "
            "{blocked} other jobs waiting on I/O on average"
        ),
        "reports.company_col.user": "User",
        "reports.company_col.job": "Job",
        "reports.company_col.cpu_peak": "CPU peak",
        "reports.company_col.mem_peak": "Memory peak",
        "reports.company_alone": "No other job was seen using the server at that time.",
        "reports.company_mount": (
            "{mount} - {ops} ops, {rtt}ms round trip, {queue}ms queued"
        ),
        "reports.company_mount_note": (
            "  Queue larger than round trip means the bottleneck is our RPC slots,"
            " not the filer."
        ),
        "reports.resource_heading": "[Resource change during scan]",
        "reports.resource_run": "{started} · {trigger} · {duration} · {status}",
        "reports.resource_other_runs": "Other runs in the same period (they added load too):",
        "reports.trigger.cron": "automatic (cron)",
        "reports.trigger.gui": "started by hand from the window",
        "reports.trigger.terminal": "started by hand from a terminal",
        "reports.duration_minutes": "{minutes} min",
        "reports.duration_hours": "{hours}h {minutes}m",
        # -- Weekday vs weekend night load comparison ---------------
        "reports.night_heading": "[Night load - weekday vs weekend]",
        "reports.night_intro": (
            "Last {days} days. 'Delta' is the peak measured against that night's "
            "own pre-scan baseline, so nights with different ambient load are "
            "still comparable. A night is classified by the MORNING IT ENDS ON - "
            "Friday night counts as weekend, Sunday night does not."
        ),
        "reports.night_weekday": "Weekday",
        "reports.night_weekend": "Weekend",
        "reports.night_col.kind": "Kind",
        "reports.night_col.count": "Nights",
        "reports.night_col.parallel": "Parallel",
        "reports.night_col.load_delta": "load delta",
        "reports.night_col.iowait_delta": "iowait delta",
        "reports.night_col.load_peak": "load peak",
        "reports.night_col.iowait_peak": "iowait peak",
        "reports.night_col.duration": "Scan time",
        "reports.night_col.date": "Date",
        "reports.night_col.status": "Status",
        "reports.night_hours": "{hours} h",
        "reports.night_caveat": (
            "Figures are medians across nights, so one unusually heavy night does "
            "not skew them. A larger weekend delta is fine IF scan time dropped "
            "too - the same work finished sooner. If load rose and scan time did "
            "not fall, parallel is not paying off on this machine: lower "
            "weekend_parallel_accounts."
        ),
        "reports.night_detail_heading": "Per night",
        "reports.night_detail_more": "  ... and {count} more nights",
        "reports.resource_context": (
            "{samples} samples - {parallel} volume(s) scanned concurrently"
        ),
        "reports.resource_caveat": (
            "These numbers are what this server saw. The real cost of du/find is "
            "usually I/O on the file server, which this process cannot observe. "
            "Low CPU with raised iowait and load does not mean there was no load."
        ),
        "reports.resource_timeline_heading": "Timeline (elapsed since scan start)",
        "reports.resource_col.metric": "Metric",
        "reports.resource_col.before": "Before",
        "reports.resource_col.average": "Avg during",
        "reports.resource_col.peak": "Peak",
        "reports.resource_col.delta": "Delta",
        "reports.resource_col.elapsed": "Elapsed",
        "reports.resource_col.accounts": "Accounts",
        "reports.resource_metric.load_avg": "load(1m)",
        "reports.resource_metric.cpu_iowait": "iowait",
        "reports.resource_metric.cpu_busy": "CPU",
        "reports.resource_metric.memory_used": "Memory",
        "reports.resource_metric.scan_share": "Scan share",
        "reports.scan_compared_with": "Baseline: compared with the {previous} scan",
        "reports.scan_heading": "{account} ({run} scan)",
        "reports.scan_progress": (
            "Progress: {done:,}/{total:,} directories done ({percent}%) - {pending:,} pending"
        ),
        "reports.scan_last_path": "Last processed: {when} - {size}",
        "reports.scan_failed_header": "[!] {count} path(s) could not be measured:",
        "scan.close_while_running_title": "Detail scan running",
        "scan.close_while_running_body": (
            "A detail scan is running. Closing the window stops it too.\n\n"
            "Directories already finished are kept; only the one in progress is "
            "redone on the next scan.\n\nClose?"
        ),
        "scan.stopped_on_close": "Stopped {count} running task(s).",
        "scan.btn.progress": "View progress...",
        "scan.btn.progress_tooltip": (
            "Shows how far the selected account has been walked, path by path (read-only)."
        ),
        "scan.current_target": "Now: {account} - {kind} - {path}",
        "scan.scanning_now": "Detail scan running - {path}",
        "progress.title": "Detail scan progress",
        "progress.kind_label": "What",
        "progress.kind.baseline": "Size - how much each directory holds",
        "progress.kind.activity": "Changes - files touched since the last scan",
        "progress.btn.refresh": "Refresh",
        "progress.summary": (
            "{generation} scan - done {done} / pending {pending} / split {split} / "
            "failed {error} (total {total})"
        ),
        "progress.col.path": "Path",
        "progress.col.status": "Status",
        "progress.col.result": "Result",
        "progress.col.scanned_at": "Processed at",
        "progress.status.pending": "Pending",
        "progress.status.done": "Done",
        "progress.status.split": "Split",
        "progress.status.error": "Failed",
        "progress.changed": "{count:,} changed",
        "notify.urgent_prefix": "Act now",
        "scan.new_path": "New (absent in previous scan)",
        "scan.no_change": "No change",
        "scan.confirm_title": "Run detail scan",
        "scan.confirm_body": (
            "This runs the detail scan now, regardless of the nightly window.\n"
            "du/find will walk the target filesystem and may add load.\n\n"
            "Continue?"
        ),
        "scan.already_running": "A detail scan is already running.",
        "scan.started": "Detail scan running...",
        "scan.auto_started": "Entered the night window - detail scan started.",
        "cron.ok": "cron: both the collector and the nightly scan are registered.",
        "dashboard.forecast_failed": "Could not compute the FULL forecast (samples are fine).",
        "cron.none": (
            "Nothing is registered in cron - close this window and neither "
            "collection nor the nightly scan will run. Run setup_cron.csh once."
        ),
        "cron.nightly_missing": (
            "The nightly scan is not in cron - with this window closed, nothing "
            "runs at night. Run setup_cron.csh, or turn on 'Nightly auto scan' "
            "in settings."
        ),
        "cron.collector_missing": (
            "The 15-minute collector is not in cron - usage history has gaps "
            "whenever this window is closed, which skews the forecast."
        ),
        "cron.nightly_by_gui": (
            "This window handles the nightly scan (not in cron). Close it and "
            "that night is skipped."
        ),
        "cron.unknown": "Could not check what is registered in cron.",
        "scan.stop_requested": "Stop requested. The scan will stop safely at its next checkpoint.",
        "scan.nothing_running": "No detail scan is running.",
        "scan.not_started": "Detail scan not started: {reason}",
        "scan.finished": "Detail scan finished (status: {status})",
        "scan.failed": "Detail scan error: {message}",
        "scan.window_open": "Nightly window open (about {minutes} min remaining)",
        "scan.window_closed": "Outside nightly window (starts {start:02d}:00, ends {end:02d}:00)",
        # -- Accounts dialog ---------------------------------------
        "accounts.title": "Accounts / Settings",
        "accounts.registered": "Registered accounts",
        "accounts.col.name": "Name",
        "accounts.col.path": "Path",
        "accounts.col.owner": "Added by",
        "accounts.col.added": "Added",
        "accounts.col.scanned": "Last scan",
        "accounts.col.kind": "Kind",
        "accounts.parallel_weekday": "Concurrent scans (weekday)",
        "accounts.parallel_weekend": "Concurrent scans (weekend)",
        "accounts.parallel_hint": (
            "How many separate volumes to scan at the same time. 1 keeps the "
            "current one-at-a-time behaviour. Accounts that live on the SAME "
            "volume always run one at a time no matter how high this is - "
            "measurements showed that hammering one volume from several "
            "threads does not go any faster. A weekend night is one that ENDS "
            "on a Saturday or Sunday morning - Friday night counts as "
            "weekend, Sunday night does not (everyone is back on Monday "
            "morning)."
        ),
        "accounts.suffix.accounts": "",
        "accounts.auto_scan": "Nightly auto scan (while this window is open)",
        "accounts.auto_scan_hint": (
            "While this window is open, the detail scan starts by itself once "
            "the night window opens (22:00 by default). It starts once per "
            "night and **stops when you close the window**.\n\n"
            "The cost: if nobody leaves the window open, that night is skipped "
            "entirely. For scans that run with nobody logged in, turn this off "
            "and register cron instead (setup_cron.csh).\n\n"
            "Leaving both on is safe - a lock keeps only one running."
        ),
        "accounts.engine": "Measured by",
        "accounts.engine.python": "Python walk (faster)",
        "accounts.engine.du": "Run du (the old way)",
        "accounts.engine_hint": (
            "How the detail scan measures sizes. The Python walk keeps several "
            "requests in flight so the NFS round trip stops being the limit - "
            "on the target machine the same account took 75-88s with du and "
            "36s with the walk, and the totals matched exactly.\n\n"
            "du runs as a separate process, so nice/ionice can lower its "
            "priority. The walk runs inside this program, where that prefix "
            "does not apply - put 'nice -n 10 ...' on the cron entry "
            "instead.\n\n"
            "If a night goes wrong, switch back to du here; nothing else needs "
            "to change."
        ),
        "accounts.col.backup_link": "Backup account",
        "accounts.kind": "Account kind",
        "accounts.kind_hint": (
            "New tasks (*_run_*) are only detected on project accounts. "
            "Backup accounts grow monotonically by design and must not be read "
            "with the same eye."
        ),
        "accounts.backup_link_none": "(not linked)",
        "accounts.backup_link_needs_backup_account": (
            "No backup account available. Register an account whose kind is "
            "'backup' first."
        ),
        "account.kind.unset": "Unset",
        "account.kind.project": "Project",
        "account.kind.backup": "Data backup",
        "accounts.advanced_settings": "Advanced settings",
        "accounts.name_placeholder": "Account name (e.g. project_a)",
        "accounts.path_placeholder": "Monitored path (e.g. /user/project_a)",
        "accounts.btn.browse": "Browse...",
        "accounts.btn.add": "Add account",
        "accounts.btn.remove": "Remove selected account",
        "accounts.browse_title": "Select monitored directory",
        "accounts.input_required_title": "Input required",
        "accounts.input_required_body": "Enter both an account name and a path.",
        "accounts.add_failed": "Could not add account",
        "accounts.remove_title": "Remove account",
        "accounts.remove_body": "Remove account '{name}' from the list? (collected history is kept)",
        "accounts.save_failed": "Save failed",
        "accounts.global_settings": "Global settings",
        "accounts.interval": "Collection interval",
        "accounts.cooldown": "Notification cooldown",
        "accounts.retention": "Sample retention",
        "accounts.language": "Display language",
        "accounts.notification_mode": "Notification mode",
        "accounts.notification_command": "Notification command (JSON array)",
        "accounts.notification_webhook": "Notification webhook URL",
        "accounts.quota_command": "Quota command (JSON array)",
        "accounts.suffix.minutes": " min",
        "accounts.suffix.days": " days",
        "accounts.none_selected_title": "No accounts",
        "accounts.none_selected_body": "Register an account first.",
        # -- Readability -------------------------------------------
        "readability.title": "Read permission check",
        "readability.all_ok": "All {checked} directories checked are readable.",
        "readability.all_ok_partial": (
            "All {checked} directories checked are readable (sampled subset only)."
        ),
        "readability.some_unreadable": (
            "{unreadable} of {checked} directories checked are not readable. "
            "Their contents are excluded from size measurement, so sizes will be understated."
        ),
        "readability.truncated_note": "(Large path - only a sample was checked.)",
        "readability.more": "... and {count} more",
        "readability.root_unreadable": "This path itself cannot be read.",
        "readability.register_anyway": (
            "Register anyway? df-based usage and alerts still work correctly; "
            "only detail-scan sizes should be read as a lower bound."
        ),
        # -- Notifications -----------------------------------------
        "notify.mode.outbox": "File outbox",
        "notify.mode.command": "Internal command (stdin)",
        "notify.mode.webhook": "Internal webhook",
        "notify.mode.disabled": "Disabled",
        "notify.message": "[{tier}] {account} ({path}) - capacity {byte_pct} / inode {inode_pct}",
        "notify.growth_message": (
            "[Path growth] {account} - {path} grew by {delta} since the last scan (now {current})"
        ),
        "notify.full_forecast_message": (
            "[FULL imminent] {filesystem} - expected to fill in about {hours}h "
            "(accounts: {accounts})"
        ),
        "notify.surge_message": (
            "[Account surge] {filesystem} - grew {delta} in the last {window}h "
            "(accounts: {accounts})"
        ),
        # -- Forecast display --------------------------------------
        "forecast.column": "Full ETA",
        "forecast.unavailable": "No estimate",
        "forecast.hours": "~{hours}h",
        "forecast.days": "~{days}d",
        "forecast.within_hour": "within 1h",
        "forecast.pair": "{short} / {long}",
        "forecast.tooltip": (
            "7-day trend: {short}\n30-day trend: {long}\n"
            "Slope over last {window}h: {slope}\n"
            "* Filesystem-wide usage; this is an estimate."
        ),
        "forecast.reason.insufficient_samples": "Not enough samples",
        "forecast.reason.not_growing": "Not trending up",
        "forecast.reason.too_far": "Beyond forecast range",
        # -- First run ---------------------------------------------
        "firstrun.title": "Getting started",
        "firstrun.heading": "Welcome to Storage Manager VWP",
        "firstrun.body": (
            "No accounts are registered yet. Review the diagnostics below, then "
            "register the account path you want to monitor.\n\n"
            "Collected data is stored separately from monitored accounts, at:\n{path}\n\n"
            "Monitored paths are never written to or deleted from."
        ),
        "firstrun.add_account": "Register an account",
        "firstrun.later": "Later",
        # -- Diagnostics -------------------------------------------
        "diagnostics.title": "Diagnostics",
        # -- Reports -----------------------------------------------
        "reports.title": "Reports",
        "reports.daily": "Daily report",
        "reports.weekly": "Weekly report",
        "reports.cleanup": "Cleanup candidates",
        "reports.generate": "Generate now",
        "reports.generated": "Report generated: {path}",
        "reports.none": "No reports generated yet.",
        # -- Search ------------------------------------------------
        "search.title": "Search (admin)",
        "search.pin_title": "Admin check",
        "search.pin_prompt": "Enter the admin PIN:",
        "search.pin_wrong": "Incorrect PIN.",
        "search.pin_caveat": (
            "* The PIN limits UI exposure only; it is not an OS permission "
            "boundary or encryption."
        ),
        "search.query_placeholder": "File / directory name",
        "search.btn.run": "Search",
        "search.mode.exact": "Exact match",
        "search.mode.prefix": "Prefix",
        "search.mode.contains": "Contains",
        "search.enable_indexing": "Enable search indexing for this account",
        "search.not_indexed": "Search indexing is off for this account.",
        "search.not_indexed_hint": (
            "Search indexing is off for this account. Turn on 'Enable search indexing' above "
            "to start (only names, extensions and paths are stored - never file contents)."
        ),
        "search.no_account": "No account to search. Register one from 'Accounts / Settings' first.",
        "search.empty_query": "Type a query, then press Enter or click Search.",
        "search.searching": "Searching...",
        "search.no_results": "Nothing matches '{query}' (out of {indexed:,} indexed entries). Try the 'contains' mode.",
        "search.index_empty": "The index is empty. Search again once indexing finishes.",
        "search.indexing_started": "Started indexing {account}. You will be notified when it finishes.",
        "search.indexing_in_progress": "Indexing is in progress. Search after it finishes (results may be partial now).",
        "search.index_done": "Indexing finished: {count:,} entries",
        "search.index_failed": "Indexing failed: {message}",
        "search.col.path": "Relative path",
        "search.col.kind": "Kind",
        "search.result_count": "{count} result(s) (showing up to {limit})",
        "search.db_size": "Search DB size on disk: {size}",
        "search.change_pin": "Change PIN...",
        "search.pin_default_warning": "Still using the default PIN. Changing it is recommended.",
        "pin.change_title": "Change admin PIN",
        "pin.current": "Current PIN",
        "pin.new": "New PIN",
        "pin.confirm": "Confirm new PIN",
        "pin.mismatch": "The new PIN entries do not match.",
        "pin.too_short": "The PIN must be at least {min_length} characters.",
        "pin.current_wrong": "The current PIN is incorrect.",
        "pin.changed": "PIN changed.",
    },
}

_current_language = DEFAULT_LANGUAGE


def available_languages() -> List[str]:
    return list(_CATALOG.keys())


def language_name(language: str) -> str:
    return LANGUAGE_NAMES.get(language, language)


def is_supported(language: str) -> bool:
    return language in _CATALOG


def set_language(language: str) -> str:
    """현재 언어를 바꾸고 실제로 적용된 언어를 반환한다.

    지원하지 않는 값이면 조용히 기본 언어로 되돌린다 - 설정 파일에 잘못된
    값이 들어 있어도 앱이 뜨지 않는 일은 없어야 한다."""

    global _current_language
    _current_language = language if is_supported(language) else DEFAULT_LANGUAGE
    return _current_language


def get_language() -> str:
    return _current_language


def t(key: str, **kwargs) -> str:
    """키를 현재 언어 문자열로 바꾼다. 없으면 기본 언어, 그것도 없으면 키 자체.

    `str.format` 인자가 모자라거나 남아도 예외를 던지지 않는다 (번역 문자열의
    사소한 불일치로 화면이 죽는 것보다 원문이라도 보이는 편이 낫다)."""

    template = _CATALOG.get(_current_language, {}).get(key)
    if template is None:
        template = _CATALOG.get(DEFAULT_LANGUAGE, {}).get(key)
    if template is None:
        return key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template
