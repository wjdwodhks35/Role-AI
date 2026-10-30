# P2-A 기존 평가 코드 분석·선별 재사용

시작 dev `cb73dd2230c2c0fc451bb5fe0cbed8c965913431`, feature/p2-a-evaluation-reuse.
GitHub와 Git 객체를 직접 비교했다. blob/선별 함수 줄 위치는 P2-A_SOURCES.json에 고정한다.

## 확인된 사실

- PR #30은 이미 merged, merge `a6e873c099a8a7af990ecd23b6d84d5b5fddaa5a`이며 시작 dev의 조상이다.
- 시작 dev의 coding_agent/jobs/edits/memory 및 기존 bench/report 파일은 PR30 merge와 blob이 동일하다. 중복 개발/재병합하지 않는다.
- PR #31 head `7e691312c5494dfce0c2168084a0fac745241985`는 open/unmerged. 그 PR의 오래된 설명에 있는 PR30 미병합 문구를 현재 사실로 사용하지 않는다.
- PR31 전체 diff에는 PR30 기반 변경이 섞여 있어 최신 dev와 비교했다. 실제 차이는 context/auto tests/정산/평가 등 별도 영역이다.
- PR31 보고서의 과거 후보 12/20→8/20 회귀/68회 중단은 역사적 실험이다. 최종 head의 실모델 결과로 적용하거나 성공 개선으로 표현하지 않는다.

## 채택/제외

| 출처 | 결정 | 실제 재사용/다음 연결 |
|---|---|---|
| 이미 dev인 PR30 LocalCodingModel/parse_reply/model_reply | 재사용 | 로컬 전송/응답 구조·backend reported usage. 실제 위임은 P2-C |
| PR30 CodingJobStore / 기존 Workspace | 유지·재사용 | private checksum/atomic/fsync/lock 및 원본 보존, 공통 Task 기반은 이미 결합 |
| 기존 RepositoryTestRunner/CodeExecutor | 재사용 | digest-pinned, network-off/제한 컨테이너. 독립 oracle 채점은 P2-B에서 연결 |
| PR31 CheckpointWriteError/save/lock passthrough | 선별 적용 | OSError 호환 유지, 이전 파일 보존, lock 오류로 오인하지 않음 |
| PR31 bounded failure_code | 분리 적용 | 새 task_errors, 타입/최대5 cause만 확인. 실제 CodingBoundaryError 타입 사용, 원문 미복사 |
| 기존 HostRunner | 제외 | 모델 생성 코드를 host subprocess로 실행하지 않음 |
| 기존 unload_all | 제외 | 다른 모델/프로젝트 캐시를 전체 unload하지 않음 |
| 기존 VRAM sampler peak=0/오류 무시 | 제외 | 미측정=0 오인 방지, 실제 sampler는 P2-D |
| 기존 report 성공/시간·토큰 평균 | 제외 | harness_error 제외/미측정 부분합/누락값 의미를 그대로 평가로 사용하지 않음 |
| PR31 auto tests/context overrides/환불·새 정산 | 제외 | 기존 승인/워크플로/예산 semantics를 한꺼번에 변경하지 않음. automatic_tests 필드 미도입 |
| PR31 기존 holdout/metrics script 전체 | 제외 | 고정 holdout의 독립성·명시 측정 상태/불완전 rows를 새 평가 요구에 맞게 검증해야 함 |

기존 qa/bench_* 파일은 역사적 자료로 보존하되 새 runner에서 import/실행하지 않는다.
새 평가가 모델이 말하는 passed/finished나 성공한 행만으로 점수를 만들지 않도록 P2-B/D에서 검증한다.

## 이번 구현

- PR31의 좁은 storage 오류/유형 진단만 이식. 기존 ModelOptions/TaskCreate/TaskContract/digest/파일·예산·승인 경계는 유지한다.
- typed checkpoint 오류는 lock을 통과해 보존한다. atomic save 실패 시 이전 checkpoint/임시 파일 정리 유지.
- legacy resume API에서 저장 실패는 고정503, 성공 상태나 private exception text 미반환. 기존 owner/CSRF 그대로.
- common Runtime 저장도 같은 타입으로 분류하되 IO/permission 실패를 자동 재시도하지 않는다.
- 새 helper/변경된 store는 승인 environment의 구현 지문에 포함한다. 새로운 권한·모델 실행·운영 설정 변경 없음.

## 10 Pass 자체 검토

06_10회_재검토_체크리스트 적용, 독립 AI 평가 아님.

| Pass | 새 발견·조치 | 잔여 위험 |
|---:|---|---|
| 1 원 요구 | PR30 재개발 위험 → GitHub merged/ancestor/blob 동일 확인 | 옛 PR 설명만으로 현재 상태 판단 금지 |
| 2 모순 | PR31 diff에 PR30 포함 → 최신 dev 대비 실제 변경만 선택 | PR31 전체 통합 안 함 |
| 3 기술 사실 | 옛 회귀 결과를 최종 head 성능으로 오인 가능 → historical/미측정 구분 | 실제 성능은 P2-E |
| 4 현실성 | 새 평가 전에 host runner 실행 위험 → 원본 읽기만, script 미실행 | 실제 Docker 검증은 기존 CI/후속 새 grader CI |
| 5 구조 | checkpoint I/O가 lock 오류로 바뀜 → 선별 typed passthrough 및 기존 OSError 호환 | 일부 일반 원자 파일 오류는 기존 포괄 진단 유지 |
| 6 보안/복구 | 예외 원문·path 유출 → type-only 고정 code/API503, 이전 파일 보존 | 원인 세부 내용은 로컬 debugger/context에서 확인 |
| 7 자원 | global unload/VRAM0/unknown refund 위험 → 해당 함수·정산/집계 미채택 | 실제 측정 파이프라인은 P2-D |
| 8 결과 예측 | 저장 실패를 실행 완료로 오인 → 성공 응답 금지·불명확 결과 보존 | storage 복구 후 수동 확인 필수 |
| 9 적대 반론 | exception cycle/가짜 타입명으로 진단 왜곡 → 5단계 제한·실제 타입 검사 | 진단 code는 권한/성공 증거 아님 |
| 10 통합 | 좁은 port가 승인·옵션을 바꿀 위험 → automatic_tests 미도입·기존 안정성/공통 상태 회귀 | 최종 CI/dev 병합 전 미완료 |

## 검증/롤백

관련 선별/기존 Coding·공통 resource53 PASS, 보완 후 runtime/안정성/공통 state109 PASS.
신규 테스트는 synthetic Git/파일/오류·HTTP이며 실제 모델/개인 데이터/운영 DB 미사용.
롤백은 좁은 helper·오류 타입/adapter revert이며 checkpoint/DB/기존 자료를 삭제하지 않는다.
실제 평가·토큰 절감 결과를 이 단계에서 주장하지 않는다. 최종 QA/CI/PR/merge는 확인 후 기록한다.

## 최종 로컬 검증

- 전체 Excel QA 436 시나리오·838회 PASS, ordered=true/exit0.
- 기존430개 계획 행 유지, 신규6개 시나리오·8회. updater 재실행0.
- `/tmp/role-ai-p2a-verified-yej34_so` Python/QA125개 지문 일치, socket/Docker/Ollama/GPU 명령 차단·별도 HOME/TMPDIR.
- Ruff 전체/diff 통과, 기존 Starlette/httpx warning1. 실제 성능/토큰 절감 미측정.
- CI/충돌·리뷰/dev 병합 완료 전 다음 단계 구현 금지.
