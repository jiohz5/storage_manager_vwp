# VWP Acceptance Checklist

이 문서는 로컬 구현 검증과 실제 RHEL/VWP에서만 확인할 수 있는 검증을 구분합니다.
아래 VWP 항목이 모두 통과해야 운영 완료로 판단합니다.

## 이미 확인된 로컬 증거

| 요구사항 | 증거 | 상태 |
|---|---|---|
| 폐쇄망, 가벼운 배포 | runtime 소스만 포함하고 wheel, 데이터, cache 제외 | 통과 |
| Python GUI | PyQt5 import 및 offscreen GUI smoke test | 통과 |
| `/user` 하위 계정 제한 | 실제 경로 및 symlink 범위 검증 단위 테스트 | 통과 |
| 사용률 경고 | `df` 파서와 80/90/95 색상 테스트 | 통과 |
| DB 크기 제한 | 하루 단위 upsert 및 365일 정리 테스트 | 통과 |
| 상세 증가량 | 전체 top-level 기준선 및 delta 테스트 | 통과 |
| 일간/주간 보고서 | scheduler 통합 테스트 | 통과 |
| cron 독립 실행 | cron 생성, 중복 marker 및 프로세스 잠금 테스트 | 부분 통과 |

`cron` 실제 등록, RHEL coreutils, VWP Qt 화면, 실제 `/user` 권한과 대용량 성능은
개발 환경만으로 증명할 수 없으므로 아래에서 확인합니다.

## 1. 전송 파일 무결성

압축 파일과 `.sha256` 파일을 같은 디렉터리에 옮긴 뒤 실행합니다.

```csh
sha256sum -c storage_manager_vwp-source.tar.gz.sha256
```

결과가 `OK`여야 합니다.

## 2. 압축 해제와 Python 지정

```csh
tar -xzf storage_manager_vwp-source.tar.gz
cd storage_manager_vwp
setenv STORAGE_MANAGER_PYTHON_HOME /path/to/python-3.10
chmod +x run.csh setup_cron.csh
```

소스가 개인 홈에 있고 데이터만 다른 위치에 둘 경우 다음 환경변수도 지정합니다.

```csh
setenv STORAGE_MANAGER_DATA_DIR /large/private/path/storage-manager-data
```

여러 관리자가 동일한 데이터 디렉터리와 SQLite 파일을 공유하지 않습니다.

## 3. RHEL/VWP 환경 검사

데이터 환경변수를 지정하지 않았다면 `--data-dir ./data`를 사용합니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 verify_environment.py \
  --data-dir ./data --monitored-root /user
```

합격 기준:

- 마지막 줄이 `0 FAIL`
- Qt platform이 `xcb` 또는 사내 환경에서 사용하는 정상 플랫폼
- `/user`가 readable
- `df`, `du`, `find`, `crontab`, `csh`가 모두 `OK`
- NFS 계열 경고가 있으면 데이터 디렉터리가 관리자 전용인지 확인

## 4. GUI와 계정 검사

```csh
./run.csh
```

GUI에서 확인합니다.

- 창이 정상적으로 열림
- 상단 언어 메뉴의 KOR/ENG 전환 즉시 모든 탭과 보고서 언어가 바뀜
- 90~94% 계정은 `주의/WARN`, 95% 이상은 `경고/ALERT`로 표시됨
- byte와 inode 사용률이 각각 표시되고 둘 중 하나가 95% 이상이면 경고됨
- `Accounts`에서 읽기 가능한 `/user/<account>` 등록 가능
- `/tmp`, `/`, `/user/account/subdir` 등록은 거절됨
- `Dashboard` 새로고침 후 `df .`와 같은 filesystem/use% 표시
- 오류 계정이 있어도 창이 멈추지 않음

## 5. 보고서 검사

등록한 계정이 있는 상태에서 상세 `du`를 생략해 빠르게 일간·주간 보고서를 모두
생성합니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 nightly_scan.py \
  --data-dir ./data --skip-detail --force-weekly
```

다음 파일이 생성되고 등록 계정이 표시되어야 합니다.

```text
data/reports/latest_daily.txt
data/reports/latest_daily_ko.txt
data/reports/latest_daily_en.txt
data/reports/latest_weekly.txt
data/reports/latest_weekly_ko.txt
data/reports/latest_weekly_en.txt
data/reports/latest_cleanup.txt
data/reports/latest_cleanup_ko.txt
data/reports/latest_cleanup_en.txt
```

별도 데이터 환경변수를 사용했다면 `./data` 대신 해당 경로를 사용합니다.

## 6. 상세 스캔 검사

업무 부하가 허용되는 시간에 한 번 실행합니다.

```csh
$STORAGE_MANAGER_PYTHON_HOME/bin/python3 nightly_scan.py --data-dir ./data
```

합격 기준:

- timeout 또는 권한 오류 없이 완료하거나, 실패 계정이 보고서에 명확히 기록됨
- 두 번째 정상 상세 실행부터 `Trend` 탭에 증가 경로가 표시됨
- 진행 중 기준선이 GUI에 작업 수로 표시되고 다음 야간 실행에서 이어짐
- 계정별 1시간과 05:45 안전 종료를 넘겨 무기한 실행되지 않음
- 동시 두 번째 실행은 `Another nightly scan is already running`으로 종료됨

## 7. cron 검사

```csh
./setup_cron.csh
crontab -l | grep storage-manager-vwp
```

22시 수집 행과 07시 건강 점검 행이 각각 하나, 총 두 줄이어야 합니다. 다음 실행 후
확인합니다.

```text
data/nightly_scan.log
data/reports/latest_daily.txt
data/reports/latest_cleanup.txt
```

로그와 보고서 날짜가 갱신되면 GUI 종료 상태의 독립 실행까지 검증된 것입니다.

`추적` 탭에서도 다음을 확인합니다.

- cron 상태는 `등록됨 (22:00 수집 + 07:00 건강 점검)`이고 상시 PID가 없어도 정상
- `지금 백그라운드 실행` 후 PID와 현재 계정이 표시됨
- `안전 중지` 후 상태가 `사용자 중지`가 되고 기준선 체크포인트가 유지됨
- `중지 후 재시작`은 기존 PID 종료 후 새 PID로 시작함
- `cron 해제`는 `storage-manager-vwp` 표시 행만 제거하고 다른 cron 행은 유지함

## 8. 내부 알림 검사

사내 command 또는 webhook 정보를 설정 탭에 입력하고 `테스트 알림`을 실행합니다.

합격 기준:

- 사내 메시지가 실제 수신됨
- `data/notifications/`에 같은 알림의 UTF-8 감사 JSON이 생성됨
- `추적` 탭에 최근 알림 시각, 모드, 전송 건수와 오류 없음이 표시됨
- 잘못된 endpoint로 시험하면 `_FAILED.json`과 오류 내용이 남음
- GUI를 닫은 상태에서 `health_check.py --data-dir ./data`를 실행해도 알림이 전송됨

## 최종 판정

1~8번이 통과하면 실제 VWP 운영 요구사항이 검증된 것입니다. 실패 출력은 수정 없이
그대로 보관하면 원인 진단에 사용할 수 있습니다.
