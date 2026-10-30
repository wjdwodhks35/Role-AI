# P1-06 상태·체크포인트 검토

시작 dev `d4dbc2d9ca2ebe4382c61bf63212e2aed961509b` (P1-05 PR #39), feature/p1-06-task-checkpoints.
공유 문서 확정 정책과 최신 사용자 단계 정의를 함께 적용한다. 미병합 PR #23/#24/#26/#28/#31/#32/#33은 유지한다.
새 task_checkpoints/task_state_api는 기존 PR을 통합하지 않으며 CodingJobStore는 변경 없이 재사용한다.

## 수용 기준과 구현

- 불변 TaskContract와 TaskExecutionState 관측을 분리, P1-01 schema/digest 그대로 유지.
- proposed/approved/queued/running/verifying/awaiting_approval/waiting/paused/interrupted/succeeded/failed/cancelled/blocked/budget_exhausted 상태를 명시적으로 검증한다. running→succeeded 직접 전이/실패·취소의 자동 재실행 금지.
- P1-05 현재 DB 세션·승인·프로젝트/정책·도구/자료 권한으로 queue/claim/완료를 검사한다. 체크포인트·checksum·상태 자체는 승인 증거가 아니다.
- 기존 CodingJobStore의 private directory, O_NOFOLLOW, nonblocking flock, checksum, 임시파일/fsync/replace/디렉터리 fsync를 재사용. 별도 contracts namespace, 사용자/Task ID 기반 고정 경로, 작업 ID 재사용 시 다른 계약 덮어쓰기 금지.
- revision CAS와 execute lock으로 중복 worker 차단. history/state/contract digest·사용자·시간·허용 전이를 검증한다. 이상 파일은 복구/덮어쓰기/재실행 없이 거부한다.
- 재시작 시 저장된 running/verifying는 interrupted로 표시하고 실행하지 않는다. 사용자가 새 revision을 확인한 뒤 명시적 queue를 요청해야 재개한다. 유효한 현재 승인 재검사 필수.
- pause/cancel 제어 파일은 별도 lock으로 원자 저장, cancel 우선 유지. 실행 중 요청은 worker 경계에서 처리하며 P1-07 이후 반복 실행기에 연결한다. 검증 도중 취소는 성공으로 덮지 않는다.
- 성공은 verifying 상태와 독립 runner의 계약/candidate digest·양수 테스트 수·exit 0 증거가 필요하다. 증거 생성은 신뢰된 서버 runner의 책임이며 외부 JSON의 runner 문자열만으로 실행을 승인하지 않는다. HTTP에는 execute/complete/임의 상태 변경 경로가 없다.
- 인증·CSRF API는 상태 생성/관측/현재 승인 기반 queue/제어만 제공한다. 로컬 모드, 자기 작업만 가능하며 계약 원문·세션 hash·원본 로그를 응답하지 않는다. UI 변경 없음.
- DB schema v6/기존 자료는 변경하지 않는다. 파일 체크포인트는 기록이고 모델/도구 권한이 아니다. 신규 모듈 지문을 P1-05 환경 digest에 포함해 정책 구현 변경 시 과거 승인을 무효화한다.

## 10 Pass 자체 검토

06_10회_재검토_체크리스트 적용, 독립 모델 평가 아님. 개수만으로 안전을 보장하지 않는다.

| Pass | 새 발견·조치 | 잔여 위험 |
|---:|---|---|
| 1 원 요구 | 대기/실행/검증/중단/재개·체크포인트를 실제 전이로 정의 | 반복·자원 예산은 P1-07 |
| 2 모순 | 과거 승인 상태로 queue 가능 위험 → 매번 P1-05 현재 DB 권한 | 철회·만료된 계약 재개 불가, 새 계약 필요 |
| 3 기술 사실 | checksum은 인증/HMAC 아님 → 모델이 접근 못하는 private 기록으로 한정, 현재 승인 별도 확인 | 신뢰된 OS 소유자의 악성 파일 조작 방어는 범위 밖 |
| 4 현실성 | 기존 CodingJobStore 검증된 저장/잠금 재사용, DB 추가 이행 없이 구현 | Linux/WSL flock 기반, Windows native 앱 실행 검증 아님 |
| 5 구조 | 기존 코딩 job checkpoint와 혼동 → contracts 하위 namespace 및 공통 계약 재검증 | 자동 worker는 아직 연결하지 않음 |
| 6 보안/복구 | running 잔존을 실행 허가로 오인 → interrupted 및 명시 revision 재개 | fsync 후 오류의 완료 여부는 보존 파일을 확인해야 함 |
| 7 자원 | 로그/원문·작업 무한 저장 위험 → 기존 용량·사용자16개·전이128 제한 | 자동 삭제/보존기한은 REQUESTS에 분리 |
| 8 결과 예측 | 테스트 중 cancel이 성공으로 덮일 수 있음 → control lock과 완료 전 검사 | 진행 중 외부 효과 소급 회수 불가 |
| 9 적대 반론 | status/자체 passed JSON으로 완료 조작 가능 → 임의 상태/완료 HTTP 없음, 독립 runner만 완료 책임 | 향후 평가 runner의 실제 증거 연결은 P2 |
| 10 통합 | 새 상태 엔진이 기존 agent checkpoint를 약화할 위험 → 기존 store 파일 무수정, 실패/변조/원본 보존 회귀 | 최종 CI 통과·dev 병합 전에는 단계 미완료 |

## 검증 및 롤백

합성 DB·임시 Git/파일만 사용한다. 실제 모델/운영 DB/서비스/설정·비밀키 변경 없음.
관련 승인·계약 검사 145 PASS, 상태·승인·기존 Coding runtime 77 PASS, 최종 상태 API/파일 검사 24 PASS.
저장 오류 시 이전 파일 유지·임시 파일 정리, checksum/계약/소유권/history/symlink 변조 거부,
중복 claim·과거 revision·실행 중 취소·승인 철회 후 재개 거부·재시작 관측을 확인했다.

롤백은 새 API/공통 상태 엔진 호출 중단 및 코드 revert이며 DB 다운그레이드는 필요하지 않다.
체크포인트는 삭제하지 않는다. 이전 승인 environment 지문 불일치 시 새 승인이 필요하다.
전체 회귀·CI·PR/병합 결과는 실제 확인 후 기록한다.

## 최종 검증 결과

- 최종 합성 Excel QA 402 시나리오·791회 모두 PASS, ordered=true, exit=0.
- 기존 385개 계획 행 보존, 새 17개 시나리오·24회 추가. updater 두 번째 실행 0 추가.
- 격리 복사본 `/tmp/role-ai-p106-verified-leh903qp`: Python/QA 119개 파일 지문이 최종 코드와 일치.
- 네트워크·Docker/Ollama/GPU 명령 차단 및 비밀 설정 없는 별도 HOME/TMPDIR. 실제 모델/운영 적용 없음.
- 전체 Ruff·diff 검사 통과. UI/JS 변경 없음. 기존 Starlette/httpx deprecation warning 1건.
- 최종 CI·리뷰/충돌 및 dev 병합을 확인하기 전에는 이 단계를 완료로 표시하지 않는다.
