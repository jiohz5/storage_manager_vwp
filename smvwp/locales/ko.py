"""한국어 UI 문자열.

키는 `en.py` 와 같아야 한다 - `test_i18n` 이 둘을 견준다. 없는 키는 화면에
키가 그대로 보이므로 금방 드러나지만, 보이기 전에 시험이 먼저 잡는다.
"""

STRINGS = {
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
    # -- 계정 상세 창 (홈 표에서 두 번 누르면) -------------------
    "detail.title": "{account} - 계정 상세",
    "detail.loading": "읽는 중...",
    "detail.failed": "읽지 못했습니다: {error}",
    "detail.gone": "그 계정을 찾지 못했습니다 (지워졌을 수 있습니다).",
    "detail.partial_failure": "{parts}을(를) 읽지 못했습니다. 나머지는 그대로입니다.",
    "detail.part.sample": "현재 용량",
    "detail.part.trend": "추세",
    "detail.part.forecast": "FULL 예측",
    "detail.part.scan": "야간 스캔 결과",
    "detail.part.health": "백업 대조",
    "detail.backup_link": "백업: {account}",
    "detail.now_heading": "지금",
    "detail.capacity": "사용 / 전체",
    "detail.free": "남은 용량",
    "detail.inode": "inode 사용률",
    "detail.quota": "quota",
    "detail.collected": "최근 수집",
    "detail.error": "수집 오류: {message}",
    "detail.no_sample": "아직 수집된 표본이 없습니다 (수집이 한 번 돌아야 채워집니다).",
    "detail.forecast_heading": "FULL 예측",
    "detail.scan_heading": "야간 상세 스캔",
    "detail.scan_line": "{when} 스캔 · 잰 용량 {size}",
    "detail.scan_delta": "지난 스캔 대비 {delta}",
    "detail.scan_never": "아직 완료된 상세 스캔이 없습니다.",
    "detail.health_heading": "과제 백업",
    "detail.health_line": "백업 확인 {done}건 · 확인 안 됨 {risky}건",
    "detail.health_none": "대조할 과제 실행 디렉터리가 없습니다.",
    "detail.health_no_link": "연결된 백업 계정이 없어 판정하지 않았습니다.",
    "detail.reclaimable": "정리하면 {size} 빕니다 (백업이 확인된 것만 셉니다)",
    "detail.tab.large": "큰 파일",
    "detail.tab.growth": "증가 경로",
    "detail.tab.health": "과제별 백업",
    "detail.health.col.task": "과제 / 실행",
    "detail.health.col.status": "백업",
    "detail.health.col.size": "크기",
    "detail.health.col.reclaim": "정리 가능",
    "detail.no_rows": "보여 줄 것이 없습니다 (상세 스캔이 한 번 돌아야 채워집니다).",
    "detail.btn.trend": "추세 자세히",
    "detail.btn.scan": "상세 스캔에서 보기",
    "health.status.backed_up": "확인됨",
    "health.status.missing": "백업 없음",
    "health.status.partial": "일부만",
    "health.status.no_backup_dir": "BACKUP 전",
    "health.status.no_link": "연결 없음",
    "scan.tab.summary": "요약",
    "scan.tab.accounts": "계정별",
    "scan.tab.growth": "증가 경로",
    "scan.tab.large": "큰 파일",
    "trend.title": "용량 추세",
    "trend.btn.open": "추세 보기",
    "trend.col": "추세",
    "trend.range_label": "기간",
    "trend.range_days": "최근 {days}일",
    "trend.loading": "불러오는 중...",
    "trend.failed": "불러오지 못했습니다: {error}",
    "trend.no_account": "계정을 고르면 추세가 표시됩니다.",
    "trend.no_data": "이력이 없습니다",
    "trend.no_data_in_range": "이 기간에는 표본이 없습니다. 수집기가 돌아야 쌓입니다.",
    "trend.summary": "지금 {now}% · 이 기간 최저 {low}% / 최고 {high}%",
    "trend.range": "최저 {low}% / 최고 {high}%",
    "trend.change": "기간 동안 {delta}%p",
    "trend.gaps": "표본이 없는 구간 {count}칸 (선이 끊긴 곳)",
    "trend.note": (
        "세로축은 0~100%로 고정입니다. 데이터에 맞춰 늘이면 78%에서 80%로 간 것이 "
        "화면을 꽉 채워 큰일이 난 것처럼 보입니다. 점선은 주의(90%)와 경고(95%)입니다.\n"
        "선이 끊긴 곳은 그때 수집기가 돌지 않은 것입니다 - 이어 그으면 없는 값을 "
        "있는 것처럼 보여주게 되므로 끊어 둡니다."
    ),
    # -- 무엇부터 할까: 줄을 눌렀을 때의 안내 -------------------
    "priority.hint": "줄을 누르면 지금 상황과 할 일을 자세히 봅니다",
    "guide.title": "{account} - 무엇을 하면 되나",
    "guide.title_general": "무엇을 하면 되나",
    "guide.level.critical": "지금 조치",
    "guide.level.high": "오늘 안에",
    "guide.level.medium": "알아 두기",
    "guide.section.situation": "지금 상황",
    "guide.section.why": "왜 먼저인가",
    "guide.section.steps": "무엇을 하면 되나",
    "guide.section.commands": "확인 명령 - 읽기만 하는 명령입니다",
    "guide.loading": "근거를 읽는 중...",
    "guide.no_evidence": "보여 줄 근거가 아직 없습니다 (야간 상세 스캔이 한 번 돌아야 채워집니다).",
    "guide.never_deletes": "이 도구는 파일을 지우지 않습니다. 정리는 과제 담당자에게 요청하세요.",
    "guide.btn.copy_commands": "명령 복사",
    "guide.btn.copy_path": "경로 복사",
    "guide.btn.account": "계정 상세",
    "guide.btn.scan": "상세 스캔으로",
    "guide.copied": "복사했습니다: {text}",
    "guide.partial": "{parts}을(를) 읽지 못해 근거가 일부 비어 있습니다.",
    "guide.more": " 외 {count}개",
    "guide.coarse_warning": (
        "이 중 일부는 BACKUP 폴더 안쪽을 못 보고 run 폴더 이름과 크기로만 대조했습니다. "
        "정리를 요청하기 전에 백업 쪽을 한 번 열어 보세요."
    ),
    "guide.col.task": "과제 / 실행",
    "guide.col.reclaim": "정리하면",
    "guide.col.backup_size": "백업 쪽 크기",
    "guide.col.backup_at": "백업 위치",
    "guide.col.path": "경로",
    "guide.col.size": "크기",
    "guide.col.delta": "지난 스캔 대비",
    "guide.col.status": "백업",
    "guide.col.missing": "백업에 없는 항목",
    "guide.col.file": "파일",
    "guide.col.share": "계정 비중",
    "guide.evidence.cleanup": "정리 후보 - 백업 계정에 같은 이름·크기로 있는 것이 확인된 과제",
    "guide.evidence.where": "어디가 많이 차지하나 - 지난 스캔 대비 많이 는 경로",
    "guide.evidence.no_backup": "백업이 확인되지 않은 과제",
    "guide.evidence.big_file": "이 계정의 큰 파일 (주황색이 이 줄의 파일)",
    "guide.evidence.surge": "어디서 늘었나 - 지난 스캔 대비 많이 는 경로",
    "guide.full.situation": "{account} 이(가) 속한 스토리지가 {pct}% 찼습니다.",
    "guide.full.numbers": "사용 {used} · 남은 {free}",
    "guide.full.forecast": "지금 속도면 FULL 까지: {forecast}",
    "guide.full.why_now": (
        "100% 가 되면 이 스토리지를 쓰는 모든 작업의 쓰기가 실패합니다. 저장하던 "
        "레이아웃과 돌던 시뮬레이션이 중간에 깨지고, 같은 스토리지를 쓰는 다른 "
        "계정도 함께 막힙니다. 그래서 목록 맨 위에 둡니다."
    ),
    "guide.full.why_soon": (
        "아직 급하지는 않지만 경고 구간입니다. 여유가 있을 때 비워 두면 "
        "급할 때 서두르다 실수할 일이 줄어듭니다."
    ),
    "guide.full.fix.step1": (
        "아래 정리 후보 {count}개(합계 {freeable})는 백업 계정에 같은 이름·크기로 "
        "있는 것이 확인된 과제입니다. 큰 것부터 과제 담당자에게 정리를 요청하세요."
    ),
    "guide.full.fix.step2": (
        "요청하기 전에 확인 명령으로 원본과 백업 쪽 크기를 한 번 비교해 보세요."
    ),
    "guide.full.fix.step3": "정리가 끝나면 홈의 `새로고침`을 누르면 사용률이 바로 바뀝니다.",
    "guide.full.nofix.step1": (
        "아래 목록에서 많이 늘어난 곳을 보고, 그 경로의 담당자에게 정리할 수 있는지 "
        "물어보세요. 더 자세히는 `계정 상세`의 큰 파일·증가 경로에 있습니다."
    ),
    "guide.full.nofix.step2": (
        "정리 후보가 안 나오는 흔한 이유는 백업 계정이 연결되지 않은 것입니다. "
        "`계정 관리`에서 이 계정의 백업 계정을 연결하면 다음 스캔부터 후보가 나옵니다."
    ),
    "guide.full.nofix.step3": (
        "당장 비울 것이 없으면 스토리지 관리자에게 용량 증설이나 쿼터 조정을 요청하세요."
    ),
    "guide.no_backup.situation": (
        "{account} 에서 BACKUP 단계까지 간 과제 {count}개({size})가 연결된 백업 "
        "계정({backup})에서 확인되지 않았습니다."
    ),
    "guide.no_backup.why": (
        "이 과제들은 사본이 한 벌뿐입니다. 실수로 지우거나 스토리지에 문제가 생기면 "
        "되살릴 곳이 없습니다. 용량 문제가 아니라 잃을 위험이라 높게 둡니다."
    ),
    "guide.no_backup.step1": (
        "아래 '백업에 없는 항목'이 백업 계정에서 찾지 못한 폴더 이름입니다. "
        "백업 담당자에게 복사가 됐는지 확인하세요."
    ),
    "guide.no_backup.step2": (
        "'일부만'은 백업 쪽 크기가 원본의 95% 에 못 미치는 것입니다 - 복사가 도중에 "
        "끊겼을 수 있습니다."
    ),
    "guide.no_backup.step3": "백업이 확인될 때까지 이 과제들은 정리 대상에서 빼세요.",
    "guide.no_backup.step4": "백업을 마친 뒤 다음 야간 스캔이 돌면 이 줄은 저절로 사라집니다.",
    "guide.cleanup.situation": (
        "{account} 에 백업이 확인된 과제 {count}개가 있습니다. 정리하면 {freeable} 이(가) 빕니다."
    ),
    "guide.cleanup.why": (
        "스토리지가 급하지는 않지만, 이미 백업된 과제가 원본 자리를 차지하고 있습니다. "
        "여유 있을 때 정리해 두면 급할 때 서두르지 않아도 됩니다."
    ),
    "guide.cleanup.step1": (
        "아래 목록은 BACKUP 안의 폴더들이 백업 계정에 같은 이름·비슷한 크기(95% 이상)로 "
        "있는 과제입니다."
    ),
    "guide.cleanup.step2": "과제 담당자에게 원본을 정리해도 되는지 확인하세요.",
    "guide.cleanup.step3": "요청하기 전에 확인 명령으로 원본과 백업 쪽 크기를 비교해 보세요.",
    "guide.big_file.situation": "{name} 파일 하나가 {size} 로 계정 전체의 {pct}% 를 차지합니다.",
    "guide.big_file.grew": "지난 스캔보다 {delta} 커졌습니다.",
    "guide.big_file.new": "지난 스캔의 큰 파일 목록에는 없던 파일입니다.",
    "guide.big_file.why": (
        "파일 하나가 계정을 이만큼 차지하면 대개 의도치 않게 커진 것입니다 - 끝나지 않은 "
        "시뮬레이션 출력, 남겨 둔 코어 덤프나 로그 같은 것들입니다. 하나만 정리해도 효과가 큽니다."
    ),
    "guide.big_file.step1": "확인 명령으로 파일 주인과 마지막 수정 시각을 보세요.",
    "guide.big_file.step2": (
        "수정 시각이 방금이면 돌고 있는 작업이 계속 키우는 중일 수 있습니다 - 주인에게 알리세요."
    ),
    "guide.big_file.step3": "필요 없는 파일이면 주인에게 정리를 요청하세요.",
    "guide.surge.situation": "{account} 이(가) 지난 스캔보다 {delta} 늘었습니다 (지금 {total}).",
    "guide.surge.why": (
        "하룻밤 사이 이만큼 늘면 대개 특정 작업 하나 때문입니다. 이 속도가 이어지면 "
        "FULL 이 그만큼 앞당겨집니다."
    ),
    "guide.surge.step1": "아래 목록에서 어디가 늘었는지 보세요.",
    "guide.surge.step2": "예정된 작업(새 과제 시작, 대량 시뮬레이션)이면 괜찮습니다.",
    "guide.surge.step3": "예상 밖이면 그 경로의 담당자에게 무엇이 쌓이는지 물어보세요.",
    "guide.unscanned.situation": "상세 스캔이 한 번도 끝나지 않은 계정: {names}",
    "guide.unscanned.why": (
        "정리 후보·큰 파일·급증은 야간 상세 스캔 결과로만 알 수 있습니다. 이 계정들에 "
        "대해 목록이 비어 있는 것은 '문제 없음'이 아니라 '아직 모름'입니다."
    ),
    "guide.unscanned.step1": "`상세 스캔` 탭에서 cron 등록 상태를 확인하세요 (빨간 글씨면 밤에 안 돕니다).",
    "guide.unscanned.step2": "계정이 크면 한 밤에 다 못 돌 수 있습니다 - `계정별` 탭의 진행률을 보세요.",
    "guide.unscanned.step3": "급하면 `지금 상세 스캔 실행`으로 바로 돌릴 수 있습니다.",
    "guide.coarse.situation": "과제 {count}개는 BACKUP 폴더 안쪽을 못 보고 판정했습니다.",
    "guide.coarse.why": (
        "스캔 깊이가 3이면 BACKUP 폴더까지만 기록되고 그 안의 폴더 이름은 모릅니다. "
        "그래서 run 폴더 이름과 전체 크기로만 비교했습니다 - '확인됨'이 실제보다 "
        "강하게 읽힐 수 있습니다."
    ),
    "guide.coarse.step1": "`계정 관리` → 설정에서 스캔 깊이를 4로 올리세요.",
    "guide.coarse.step2": "다음 야간 스캔부터 BACKUP 안의 폴더 이름까지 하나하나 대조합니다.",
    "guide.coarse.step3": "그 전까지는 정리를 요청하기 전에 백업 쪽을 직접 열어 확인하세요.",
    "priority.heading": "무엇부터 할까",
    "priority.summary": "할 일 {count}건 · 지금 조치 {critical}건 · 정리하면 {freeable} 빕니다",
    "priority.nothing": "지금 먼저 손댈 것이 없습니다.",
    "priority.unavailable": "우선순위를 계산하지 못했습니다. 아래 표는 그대로 볼 수 있습니다.",
    "priority.unscanned": "아직 상세 스캔이 끝나지 않은 계정 {count}개는 판단에 넣지 못했습니다 ({names})",
    "priority.coarse": "과제 {count}개는 BACKUP 안쪽까지 못 보고 판정했습니다 (설정의 `스캔 깊이`를 4로 올리면 정확해집니다)",
    "priority.item.full_shared_with_fix": (
        "{mount} · 계정 {accounts}개 {pct}% - 정리하면 {freeable} 빕니다 (백업 확인된 과제 {count}개)"
    ),
    "priority.item.full_shared": (
        "{mount} · 계정 {accounts}개 {pct}% - 비울 수 있는 것을 아직 못 찾았습니다"
    ),
    "guide.col.account": "계정",
    "guide.col.tasks": "정리 후보 과제",
    "guide.evidence.shared": "이 스토리지를 쓰는 계정들 - 정리할 것이 많은 쪽부터",
    "guide.full.shared": (
        "{mount} 을(를) 계정 {count}개가 함께 쓰고, 지금 {pct}% 찼습니다: {names}"
    ),
    "guide.full.shared_how": (
        "`df` 가 이 계정들에 같은 파일시스템·같은 크기를 돌려주어 한 자리로 묶었습니다."
    ),
    "guide.full.why_shared": (
        "한 계정만 정리해도 같은 스토리지를 쓰는 나머지 계정까지 함께 숨통이 트입니다."
    ),
    "guide.full.shared.step": (
        "아래에서 정리할 것이 가장 많은 계정부터 봅니다. `계정 상세` 로 그 계정의 "
        "과제 목록을 열 수 있습니다."
    ),
    "priority.item.full_with_fix": "{account} {pct}% - 정리하면 {freeable} 빕니다 (백업 확인된 과제 {count}개)",
    "priority.item.full": "{account} {pct}% - 비울 수 있는 것을 아직 못 찾았습니다",
    "priority.item.no_backup": "{account}: 백업이 확인되지 않은 과제 {count}개 ({size}) - 지금 사라지면 복구할 곳이 없습니다",
    "priority.item.cleanup": "{account}: 정리하면 {freeable} 빕니다 (백업 확인된 과제 {count}개)",
    "priority.item.big_file": "{account}: {name} 하나가 {size} - 계정의 {pct}%입니다",
    "priority.item.surge": "{account}: 지난 스캔보다 {delta} 늘었습니다 (지금 {total})",
    "tree.tab.list": "목록",
    "tree.tab.map": "그림",
    "treemap.btn.up": "위로",
    "treemap.btn.home": "처음으로",
    "treemap.rest": "직접 있는 파일",
    "treemap.share": "계정의 {pct}%",
    "treemap.empty": "그릴 것이 없습니다.",
    "treemap.hint": (
        "넓이가 곧 용량입니다. 조각을 두 번 누르면 그 안으로 들어갑니다. "
        "회색은 폴더가 아니라 계산으로 만든 칸이라 들어갈 수 없습니다. "
        "빨간 테두리는 눈에 띄는 것입니다."
    ),
    "digest.run": "지난 스캔",
    "digest.run_detail": "{status} · {duration}",
    "digest.running": "도는 중",
    "digest.running_detail": "끝나면 여기에 결과가 채워집니다",
    "digest.never_run": "아직 한 번도 끝나지 않았습니다",
    "digest.status.completed": "끝까지 완주",
    "digest.status.paused": "아침에 멈춤",
    "digest.status.stopped": "중지 요청으로 멈춤",
    "digest.status.error": "오류로 멈춤",
    "digest.status.running": "도는 중",
    "digest.duration_hm": "{hours}시간 {minutes}분",
    "digest.duration_m": "{minutes}분",
    "digest.delta": "전체 증가",
    "digest.delta_detail": "지난 스캔과 견준 계정 {count}개",
    "digest.no_compare": "견줄 지난 스캔이 없습니다",
    "digest.biggest": "가장 많이 는 계정",
    "digest.biggest_detail": "{delta} · 지금 {total}",
    "digest.findings": "살펴볼 것",
    "digest.findings_none": "지금은 눈에 띄는 것이 없습니다",
    "digest.findings_detail": "그중 급한 것 {urgent}건",
    "digest.findings_heading": "살펴볼 것",
    "digest.findings_empty": "눈에 띄는 것이 없습니다. 다른 탭에서 자세히 볼 수 있습니다.",
    "digest.findings_hint": "줄을 누르면 그 계정의 해당 탭으로 갑니다.",
    "digest.item.failed": "{account}: 재지 못한 경로 {count}곳 - 권한이나 오류를 확인하세요",
    "digest.item.partial": "{account}: 권한이 없어 덜 세어진 경로 {count}곳 - 실제 크기는 이보다 큽니다",
    "digest.item.unavailable_unreadable": "{account}: {when} 스캔에서 계정 경로를 열 수 없어 건너뛰었습니다 ({detail}) - 마운트·경로·권한을 확인하세요. 지난 결과를 그대로 보여 줍니다",
    "digest.item.unavailable_empty": "{account}: {when} 스캔에서 계정 경로가 비어 보여 건너뛰었습니다 - 마운트가 빠졌을 수 있습니다. 지난 결과를 그대로 보여 줍니다",
    "digest.item.large_file": "{account}: {name} - {size} ({change})",
    "digest.item.growth": "{account}: 지난 스캔보다 {delta} 늘었습니다 (지금 {total})",
    "load.title": "서버 부하 이력",
    "load.btn.open": "서버 부하",
    "load.btn.refresh": "새로고침",
    "load.range_label": "기간",
    "load.range_days": "최근 {days}일",
    "load.sort_label": "작업 정렬",
    "load.sort.cpu": "CPU 큰 것부터",
    "load.sort.mem": "메모리 큰 것부터",
    "load.loading": "불러오는 중...",
    "load.failed": "불러오지 못했습니다: {error}",
    "load.no_samples": (
        "아직 부하 표본이 없습니다. 수집기(15분 주기)가 한 번은 돌아야 쌓이기 "
        "시작하고, 시간대별로 판단하려면 며칠은 모여야 합니다."
    ),
    "load.summary": (
        "표본 {samples}벌 · 우리 스캔이 없을 때 다른 작업이 쓴 CPU {idle}, "
        "스캔 중에는 {busy} · {best}"
    ),
    "load.best_hour": "가장 한가한 때 {hour} ({verdict})",
    "load.has_room": "여유 있음",
    "load.no_room": "여유 없음",
    "load.mixed_suffix": "  (우리 스캔 섞임)",
    "load.ours_suffix": "  (우리)",
    "load.hours_heading": "시간대별 · 우리를 뺀 나머지가 쓴 CPU. 붉은 줄은 이미 붐비는 시간대입니다",
    "load.jobs_heading": "그 시간에 서버를 쓴 작업",
    "load.jobs_note": (
        "CPU 평균은 상위 목록에 들었던 표본들만의 평균입니다. 한가할 때는 목록에 "
        "못 들어 빠지므로 하루 평균보다 높게 나옵니다 - '이 작업이 평소 쓰는 양'으로 "
        "읽으면 안 됩니다. 열 제목을 눌러 정렬할 수 있습니다."
    ),
    "load.mounts_heading": "NFS 마운트",
    "load.mounts_note": (
        "왕복(rtt)은 파일서버가 답하기까지, 대기(queue)는 요청이 보내지기도 전에 "
        "우리 쪽에서 기다린 시간입니다. 대기가 더 크면 병목은 파일서버가 아니라 "
        "이쪽 RPC 슬롯이고, 그때 병렬을 줄이면 정확히 반대 처방입니다."
    ),
    "load.queue_tip": "대기가 왕복보다 큽니다 - 병목이 우리 쪽 RPC 슬롯입니다.",
    "load.col.hour": "시간대",
    "load.col.others_cpu": "다른 작업 CPU",
    "load.col.load": "load",
    "load.col.waiting": "I/O 대기 작업",
    "load.col.samples": "표본",
    "load.col.user": "사용자",
    "load.col.job": "작업",
    "load.col.cpu_peak": "CPU 최고",
    "load.col.cpu_avg": "CPU 평균",
    "load.col.mem_peak": "메모리 최고",
    "load.col.mount": "마운트",
    "load.col.ops": "요청 수",
    "load.col.rtt": "왕복",
    "load.col.queue": "대기",
    "tree.title": "폴더 펼쳐 보기",
    "tree.btn.open": "폴더 펼쳐 보기",
    "tree.btn.expand": "한 단계 더 펼치기",
    "tree.col.path": "폴더",
    "tree.col.size": "크기",
    "tree.col.share": "계정 비중",
    "tree.col.change": "지난 스캔 대비",
    "tree.caption": "디렉터리 {count}개 · 계정 합계 {total} · 야간 스캔이 남긴 기록을 읽은 것이라 지금 다시 재지 않습니다",
    "tree.loading": "불러오는 중...",
    "tree.busy": "이미 불러오는 중입니다.",
    "tree.failed": "불러오지 못했습니다: {error}",
    "tree.no_account": "계정을 고르면 폴더가 표시됩니다.",
    "tree.no_scan": "아직 완료된 상세 스캔이 없습니다. 한 번은 끝나야 안쪽을 볼 수 있습니다.",
    "tree.rest": "(이 폴더에 직접 있는 파일)",
    "tree.more": "(그 밖 폴더 {count}개)",
    "tree.file": "파일: {name}",
    "tree.new": "지난 스캔에 없던 것",
    "tree.legend": (
        "크기는 자기 자신을 포함한 합계입니다. 하위 폴더를 다 더해도 부모보다 "
        "작으면 그 차이가 '이 폴더에 직접 있는 파일'입니다.\n"
        "회색 줄은 계산으로 만든 줄이라 더 들어갈 수 없습니다. "
        "'지난 스캔 대비'를 계산하지 않는 것도 그래서입니다 - 그때의 하위 구성이 "
        "지금과 같다는 보장이 없어 그럴듯하지만 틀린 수가 됩니다."
    ),
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
    "dashboard.col.size": "사용량 / 총 용량",
    "dashboard.tip.filesystem": "파일시스템: {value}",
    "dashboard.tip.mount": "마운트: {value}",
    "dashboard.tip.used": "사용량: {value}",
    "dashboard.tip.total": "총 용량: {value}",
    "dashboard.tip.free": "남은 용량: {value}",
    "dashboard.col.byte_pct": "용량 사용률",
    "dashboard.col.collected_at": "최근 수집",
    "dashboard.col.kind": "성격",
    "dashboard.collect_error_short": "수집 실패",
    "dashboard.filter": "이름·경로로 찾기",
    "dashboard.list_filtered": "{total}개 중 {shown}개 보임",
    "dashboard.list_title": "계정 목록",
    "dashboard.list_hint": "행을 두 번 누르면 그 계정의 모든 것을 한 창에서 봅니다",
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
    "dashboard.stat.storages": "스토리지",
    "dashboard.stat.attention": "주의 이상",
    "dashboard.stat.collected": "마지막 수집",
    "dashboard.not_collected": "아직 수집되지 않음",
    "dashboard.collect_failed": "수집 실패: {message}",
    "dashboard.collecting": "수집 중...",
    "dashboard.collected": "수집 완료 ({count}개 계정)",
    "dashboard.collected_elsewhere": "방금 다른 곳(cron 등)에서 수집한 값을 보여 줍니다",
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
    # -- 메뉴 --------------------------------------------------
    "tab.home": "홈",
    "tab.scan": "상세 스캔",
    "menu.language": "언어",
    # -- 상세 스캔 ---------------------------------------------
    "scan.headline.running": "실행 중",
    "scan.headline.running_pct": "실행 중 · {percent}%",
    "scan.headline.idle": "대기 중 · 지난 실행: {status}",
    "scan.headline.never": "대기 중 · 아직 실행한 적 없음",
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
    "scan.latest_started": "시작 {started_at}",
    "scan.pending_tasks": "남은 작업 {count:,}개",
    "scan.progress_counts": "디렉터리 {done:,}/{total:,} 완료 ({percent}%)",
    "scan.progress_tip": (
        "시간 초과로 디렉터리를 쪼개면 작업이 더해져 총계가 늘어납니다. "
        "그래서 진행률이 잠깐 뒤로 갈 수 있습니다 - 고장이 아닙니다."
    ),
    "scan.paths_under": "경로는 {root} 기준",
    "scan.status_error": "스캔 상태를 읽을 수 없습니다: {message}",
    "scan.account_label": "계정",
    "scan.col.path": "경로",
    "scan.col.current_size": "현재 크기",
    "scan.col.delta": "이전 스캔 대비",
    "scan.col.delta_dated": "{previous} 대비",
    "scan.nth": "{n}번째 스캔",
    "scan.select_account": "계정을 선택하면 증가 경로가 표시됩니다.",
    "scan.no_baseline": "{account}: 아직 완료된 기준선이 없습니다 (상세 스캔이 한 바퀴 끝나야 표시됩니다).",
    "scan.growth_caption": "{account}: {current} 스캔 기준, {previous} 스캔과 같은 경로끼리 비교{notice}",
    "scan.baseline_only_caption": (
        "{account}: {current} 스캔 결과만 있습니다 "
        "(비교할 이전 스캔이 없어 증감은 다음 스캔부터 표시됩니다){notice}"
    ),
    "scan.partial_warning": (
        "⚠ 경로 {count}곳은 읽을 수 없는 하위 디렉터리가 있어 실제보다 작게 "
        "측정되었습니다 (권한 부족). 증가량도 그만큼 축소될 수 있습니다."
    ),
    "scan.cpu_usage": (
        "직전 스캔 CPU: 평균 {avg}% · 최대 {peak}% (top 기준, 코어 1개=100%) "
        "· 장비 전체의 {system}%"
    ),
    "scan.memory_usage": "메모리 최대 {peak} (장비 전체의 {percent}%)",
    "scan.failed_warning": (
        "⚠ 경로 {count}곳은 크기를 재지 못했습니다. 아래 사유를 확인하세요 "
        "(권한 부족이면 관리자에게 읽기 권한을 요청하거나 대상에서 제외하면 됩니다)."
    ),
    "scan.failed_more": "  … 외 {count}곳 (전체 목록은 주간 보고서에서)",
    "reports.scan_progress_heading": "[상세 스캔 진행 상황]",
    # -- 상세 스캔 탭: 계정별 현황 표 --------------------------
    "scan.acct.heading": (
        "행을 고르면 오른쪽 위 계정 선택이 따라오고, 두 번 누르면 그 계정의 증가 경로로 갑니다."
    ),
    "scan.acct.name": "계정",
    "scan.acct.kind": "성격",
    "scan.acct.progress": "진행",
    "scan.acct.pending": "남은 작업",
    "scan.acct.measured": "찾은 용량",
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
    "reports.large_heading": "[눈에 띄는 큰 파일]",
    "reports.large_caveat": (
        "파일 하나가 계정의 5% 이상을 차지하거나, 지난 스캔보다 1.5배 이상 "
        "커졌거나, 지난 스캔 상위 목록에 없던 것만 싣습니다. "
        "'목록에 없던 파일'은 새로 생겼다는 뜻이 아닙니다 - 그때는 작았을 "
        "수도 있습니다."
    ),
    "large.heading": "{account}: 큰 순서로 {count}개  ·  주황색은 계정 안에서 혼자 두드러지는 파일",
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
    "reports.new_tasks_basis": (
        "  기준: 직전 스캔이 그 폴더의 내용을 봤는데 없었고, 이번 스캔에 나타난 "
        "`*_run_*` 디렉터리"
    ),
    "reports.new_tasks_unverified": (
        "직전 스캔이 들여다보지 않은 자리 {count}곳은 판단을 보류했습니다 "
        "(권한 오류이거나, 시간 초과로 쪼개져 스캔이 닿은 깊이가 밤마다 달랐던 곳입니다)."
    ),
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
        "여기 숫자는 이 서버에서 본 것뿐입니다. 상세 스캔의 실제 부담은 대개 "
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
    "scan.current_target": "지금: {account} · {path}",
    "scan.scanning_now": "상세 스캔 진행 중 · {path}",
    "progress.title": "상세 스캔 진행 상황",
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
    "notify.urgent_prefix": "즉시 확인",
    "scan.new_path": "신규 (이전 스캔에 없음)",
    "scan.no_change": "변화 없음",
    "scan.confirm_title": "상세 스캔 실행",
    "scan.confirm_body": (
        "야간 시간창과 무관하게 지금 상세 스캔을 실행합니다.\n"
        "대상 파일시스템을 통째로 훑으므로 부하가 생길 수 있습니다.\n\n"
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
}
