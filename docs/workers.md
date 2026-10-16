# Worker 관리: heartbeat·진행 상황·취소·재시도·중단 복구

이번 기능은 수집 Worker와 학습 Worker를 관리합니다. 코드 실행 컨테이너와 OpenClaw는 별도이며
이 화면에서 OS 프로세스를 시작/종료하거나 임의 명령을 실행하지 않습니다. 기본 유료 API·HF 수집·학습
비활성화 설정을 변경하지 않습니다. 실제 GPU 학습 품질은 별도로 검증해야 합니다.

## 업데이트 전 필수: 중지·백업·DB v2 업그레이드

v1에서 v2로 Worker presence/control 테이블 두 개를 추가합니다. 기존 DB에서는 API와 모든 Worker를
중지하고 아래 CLI를 실행해야 합니다. 기존 행·모델·파일은 보존합니다. 새 DB는 자동 초기화합니다.

```bash
python -m role_ai.migrations status
python -m role_ai.migrations upgrade --offline --backup /안전한/새-업데이트전-백업경로
```

[백업·새 경로 복원](backup-restore.md), [마이그레이션](migrations.md)을 먼저 확인하세요.
Compose 사용 시 API·collector·호스트 학습 Worker를 중지하고 이미지만 새로 빌드한 후,
해당 데이터 volume에 접근하는 일회성 API 컨테이너에서 마이그레이션할 수 있습니다.
예를 들어 백업 대상을 `/app/data/backups/고유한-새-경로`로 지정하면 같은 data volume에 남습니다.
`docker compose down --volumes`를 실행하지 마세요. 별도 디스크 백업도 권장합니다.

## Worker 시작

```bash
# 실제 로컬 모델·승인 데이터·GPU·학습 의존성 준비 후, 학습 활성화를 명시한 설정 사용
python -m role_ai.training --once
# 지속 대기; 작업이 없으면 모델/GPU를 불러오지 않음
python -m role_ai.training --poll-seconds 10
# 기존 명시적 HF 활성화 설정을 사용
python -m role_ai.collection --once
python -m role_ai.collection
```

학습은 승인된 큐만 소비하며 자동 승인·유료 API를 새로 활성화하지 않습니다.
Ctrl+C/SIGTERM은 다음 체크포인트에서 안전하게 정지하도록 요청합니다.
유효한 파일 기반 SQLite와 API/Worker에서 같은 DB·artifact 경로가 필요합니다.
컨테이너 collector도 같은 volume의 DB를 사용합니다. 호스트 학습 Worker의 `/app/data` 경로가
자동으로 동일 volume을 가리키는 것은 아니므로 실제 경로·권한을 확인해야 합니다.

## 화면과 측정 기준

로컬 소유자로 로그인한 뒤 **Worker 관리**를 엽니다. 화면이 열린 동안 10초마다 갱신합니다.

- Worker는 5초마다 별도 스레드로 heartbeat를 저장합니다. 20초를 넘으면 오프라인으로 표시합니다.
- 종료된 Worker는 stopped/failed로 남으며 온라인으로 표시하지 않습니다. 최근 100개 프로세스를 조회합니다.
- 수집은 탐색·샘플링·저장·검수 등 실제 단계를 표시하고 전체 퍼센트는 계산하지 않습니다.
- 학습은 로딩·평가·저장 단계를 표시하고, Trainer callback에서 확인한 global_step/max_steps만 퍼센트로 표시합니다.
  학습 스텝 100%도 후속 평가·저장 완료를 뜻하지 않습니다. 응답이 끊기면 진행률은 미확인입니다.
- 최근 heartbeat는 모델 품질·GPU 가용성·작업 성공을 보장하지 않습니다.
- 작업별 결과/실패 사유와 시작·취소·재시도·완료 이력(최근 50개)을 제공합니다.
  heartbeat 원문에 토큰·프롬프트·환경 변수·호스트 경로를 저장하지 않습니다.

## 취소·재시도·복구

모든 관리 API는 로그인·CSRF·로컬 소유자 권한·작업 소유권을 검사합니다.

- `/api/workers`: 측정 상태·내 작업·이력 조회.
- `POST /api/workers/{training|collection}/{id}/cancel`: 대기 작업은 즉시 취소,
  실행 중인 작업은 취소 요청을 저장하고 다음 체크포인트에서 취소합니다.
  학습 취소 시 승인을 해제하며 결과를 자동 적용하지 않습니다.
- `POST .../retry`: 학습은 blocked 자원 대기 작업만 기존 승인 해시/계획/검수를 다시 확인해 새 작업 ID로 재등록합니다.
  이전 작업은 retried로 남고 부분 결과를 보존하므로 같은 폴더를 덮어쓰지 않습니다. 중복 대기/실행도 거부합니다.
  모델/파일/GPU는 Worker가 실행 직전에 재검증합니다. failed/cancelled/interrupted 학습은 다시 승인해야 합니다.
  수집은 실패·취소 이력과 남은 횟수, HF 활성화를 확인합니다. 소비한 횟수를 초기화하지 않습니다.
- `POST /api/workers/training/{id}/recover`: running으로 남은 학습은 OS 저장소 잠금을 실제로 획득한
  경우에만 interrupted로 바꾸고 승인을 해제합니다. heartbeat만 보고 강제 재실행하지 않습니다.
  활성 Worker가 잠금을 잡고 있으면 409로 거부합니다. 부분 결과 파일은 자동 삭제하지 않습니다.

모델 로딩/단일 GPU 연산/이미 진행 중인 HTTP·유료 요청은 즉시 중단되지 않습니다.
수집 취소는 이미 저장한 snapshot, 이미 승인·등록된 별도 학습 작업이나 비용을 되돌리지 않습니다.
필요하면 학습 큐도 따로 취소하세요. SIGKILL·전원 중단 후 학습은 수동 복구·재승인이 필요합니다.
수집의 기존 OS 잠금 기반 중단 복구와 실행 횟수 제한은 유지합니다. 자동 무한 재시도는 없습니다.

## 검증 범위

합성 데이터와 모사 학습기로 스텝 측정·취소·자원 오류와 취소의 경합·승인 검사·소유권·중단 잠금을
검사하고, 실제 별도 Python 프로세스로 대기 중 학습 Worker CLI를 실행합니다.
실제 사용자 서버의 GPU 학습/강제 종료 복구/브라우저 E2E는 별도 실환경 검증 대상입니다.
