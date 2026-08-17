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
        "dashboard.col.filesystem": "파일시스템",
        "dashboard.col.byte_pct": "용량 사용률",
        "dashboard.col.inode_pct": "inode 사용률",
        "dashboard.col.quota": "quota",
        "dashboard.col.tier": "종합 등급",
        "dashboard.col.collected_at": "최근 수집",
        "dashboard.col.status": "상태",
        "dashboard.waiting": "수집 대기 중...",
        "dashboard.btn.collect_now": "지금 수집",
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
        "menu.language": "언어",
        "menu.file": "파일",
        # -- 상세 스캔 ---------------------------------------------
        "scan.section_title": "상세 스캔 (야간 du/find 기반 증가 경로)",
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
        "scan.status_error": "스캔 상태를 읽을 수 없습니다: {message}",
        "scan.col.path": "경로",
        "scan.col.current_size": "현재 크기",
        "scan.col.delta": "이전 세대 대비",
        "scan.select_account": "계정을 선택하면 증가 경로가 표시됩니다.",
        "scan.no_baseline": "{account}: 아직 완료된 기준선이 없습니다 (상세 스캔이 한 바퀴 끝나야 표시됩니다).",
        "scan.growth_caption": "{account}: 세대 {generation} 기준, 직전 세대와 같은 경로끼리 비교{activity}",
        "scan.baseline_only_caption": (
            "{account}: 세대 {generation} 기준선만 있습니다 "
            "(비교할 이전 세대가 없어 증감은 다음 스캔부터 표시됩니다){activity}"
        ),
        "scan.activity_note": " · 최근 변경 파일 {count:,}개",
        "scan.partial_warning": (
            "⚠ 경로 {count}곳은 읽을 수 없는 하위 디렉터리가 있어 실제보다 작게 "
            "측정되었습니다 (권한 부족). 증가량도 그만큼 축소될 수 있습니다."
        ),
        "scan.new_path": "신규 (이전 세대에 없음)",
        "scan.no_change": "변화 없음",
        "scan.confirm_title": "상세 스캔 실행",
        "scan.confirm_body": (
            "야간 시간창과 무관하게 지금 상세 스캔을 실행합니다.\n"
            "du/find가 대상 파일시스템을 훑으므로 부하가 생길 수 있습니다.\n\n"
            "계속할까요?"
        ),
        "scan.already_running": "상세 스캔이 이미 실행 중입니다.",
        "scan.started": "상세 스캔 실행 중...",
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
            "[급증] {account} - {path} 이(가) {delta} 증가 (현재 {current})"
        ),
        "notify.full_forecast_message": (
            "[FULL 임박] {filesystem} - 약 {hours}시간 후 가득 참 예상 "
            "(대상 계정: {accounts})"
        ),
        "notify.surge_message": (
            "[급증] {filesystem} - 최근 {window}시간 동안 {delta} 증가 "
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
        "dashboard.col.filesystem": "Filesystem",
        "dashboard.col.byte_pct": "Capacity used",
        "dashboard.col.inode_pct": "Inode used",
        "dashboard.col.quota": "Quota",
        "dashboard.col.tier": "Overall tier",
        "dashboard.col.collected_at": "Last collected",
        "dashboard.col.status": "Status",
        "dashboard.waiting": "Waiting for first collection...",
        "dashboard.btn.collect_now": "Collect now",
        "dashboard.btn.accounts": "Accounts / Settings...",
        "dashboard.btn.diagnose": "Diagnostics...",
        "dashboard.btn.reports": "Reports...",
        "dashboard.btn.search": "Search...",
        "dashboard.no_accounts": "No accounts registered. Add one from 'Accounts / Settings'.",
        "dashboard.all_normal": "All accounts normal ({count} accounts)",
        "dashboard.warn_summary": "{count} account(s) at warning or worse - most urgent: {worst}",
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
        "dashboard.hero_detail": "Highest usage · {account}",
        "dashboard.stat.accounts": "Accounts",
        "dashboard.stat.attention": "Warning+",
        "dashboard.stat.collected": "Last collected",
        "freshness.notify_stale": (
            "[Collection stalled] {account} - last collected {age}. Check whether the "
            "collector is still running."
        ),
        # -- Menu --------------------------------------------------
        "menu.language": "Language",
        "menu.file": "File",
        # -- Detail scan -------------------------------------------
        "scan.section_title": "Detail scan (nightly du/find growth paths)",
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
        "scan.status_error": "Cannot read scan status: {message}",
        "scan.col.path": "Path",
        "scan.col.current_size": "Current size",
        "scan.col.delta": "vs previous generation",
        "scan.select_account": "Select an account to see its growth paths.",
        "scan.no_baseline": "{account}: no completed baseline yet (one full detail scan is required).",
        "scan.growth_caption": "{account}: generation {generation}, compared path-by-path with the previous generation{activity}",
        "scan.baseline_only_caption": (
            "{account}: only generation {generation} baseline exists "
            "(no previous generation to compare, deltas appear after the next scan){activity}"
        ),
        "scan.activity_note": " - {count:,} changed file(s) recently",
        "scan.partial_warning": (
            "! {count} path(s) contain unreadable subdirectories, so their sizes are "
            "under-measured (insufficient permissions). Growth figures may be understated."
        ),
        "scan.new_path": "New (absent in previous generation)",
        "scan.no_change": "No change",
        "scan.confirm_title": "Run detail scan",
        "scan.confirm_body": (
            "This runs the detail scan now, regardless of the nightly window.\n"
            "du/find will walk the target filesystem and may add load.\n\n"
            "Continue?"
        ),
        "scan.already_running": "A detail scan is already running.",
        "scan.started": "Detail scan running...",
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
            "[Growth] {account} - {path} grew by {delta} (now {current})"
        ),
        "notify.full_forecast_message": (
            "[FULL imminent] {filesystem} - expected to fill in about {hours}h "
            "(accounts: {accounts})"
        ),
        "notify.surge_message": (
            "[Surge] {filesystem} - grew {delta} in the last {window}h "
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
