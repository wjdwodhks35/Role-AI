# Coding Strategy Learner v1.1 도입안

제품 기준: 사용자 제공 v1.1 기획서 4.2-A/7절. 코드 기준: dev `d3d790a`.
본 문서는 미구현 기능의 설계 제안이다. 경험 저장·검색과 모델 가중치 학습을 구분한다.

## 현재 단계 · Coding Agent만 구현

PR #29는 R01의 strict 계약·memory/no-op 코드를 재사용하고 Coding runtime의 최소 기록을 연결한다.
기존 Workspace 관찰 계약은 test_result=not_run을 유지하며, runtime의 별도 v2 ExecutionObservation은
현재 revision의 실제 테스트 상태·phase·횟수·토큰·식별 hash만 기록한다. 검증은 observed이며
우수 전략·학습 적격 판정이 아니다. 원문 요청/코드/diff/로그는 Strategy 이벤트에 저장하지 않는다.

선택한 최소 JSONL 기록과 필수 파일 checkpoint는 서로 다른 경로다. 기록 장애는 gap으로 남기지만
checkpoint/승인 실패를 우회하지 않는다. R02의 DB 저장/삭제 관리, Retriever, Optimizer, Model
Learning 및 Preview 피드백 학습은 현재 개발 범위에서 보류한다. 아래 표/테스트는 장기 계획이며
이미 구현한 최소 구조와 혼동하지 않는다. 실제 구현·사용은 [runtime 안내](../coding-agent-runtime.md)를 따른다.

## 단계·활성화 게이트

| 단계 | PR / 시점 | 활성화 조건 |
|---|---|---|
| Recorder | R01/R02, Coding MVP 초기 C01~C03와 함께 | 동의한 최소 메타데이터 기록만. 검색/전략 선택/학습 없음. 기록 장애는 gap 표시하고 코딩 계속 |
| Retriever | S01/S02, Coding 안정화 후 Preview Lab 시점 | 다양하고 검증된 경험·만료/tenant/리비전 필터·검색 미사용 대비 독립 작업군 평가 통과. 부족하면 비활성 |
| Optimizer | O01/O02, E01 완료 후 Evolution Lab 단계 | 실제 독립 평가·불확실성/회귀/시간/자원 비교·사용자 승인·롤백 |
| Model Learning | L01/L02, 자가학습 후반 | 검증/권리/PII/동의/중복·누출 심사 통과 경험만 후보 가공. export/학습/모델 활성화 승인 분리 |

경험이 적다고 사용자 원문을 대량 수집하지 않는다. 충분성은 단순 건수보다 작업/저장소/
실패 유형 다양성과 평가 불확실성으로 판단한다. 조건부 Retriever 보류로 Preview 개발을 막지 않는다.

## 최소 이벤트 schema 제안

task 최종 행만 덮어쓰지 않고 이벤트/attempt를 구분한다. Workspace dict에는 diff 등이
섞일 수 있으므로 반환값 전체를 저장하지 않고 명시적 허용 필드 adapter를 사용한다.

| 필드군 | 제안 필드·제약 |
|---|---|
| 버전·중복 | event_version, event_id, task_id, attempt_id, sequence, event_type, payload_hash. 서버 발급 ID; 동일 ID+payload는 멱등, 다른 payload는 충돌 |
| 격리·동의 | user_id, project_id, repository_id, consent_revision. 인증된 서버 actor에서 결정, 본문 값으로 소유권 변경 금지 |
| 재현·출처 | repo_revision, task_spec_hash, strategy_id/version, prompt/policy/runner/model 식별·hash. 절대 경로/토큰 금지 |
| 문제 | task_category 및 optional problem_summary. 초기 enum만, summary=null 기본. 추가 동의·정제 후 제한 길이 허용 |
| 행동 | phase, plan_step_kind, tool_kind, attempt_count. 허용 enum만, args/명령문/모델 응답/계획 원문 없음 |
| 증거 | test_result, independent_evaluation, error_category, artifact_refs, evidence_hash. 실제 결과만, 오류 원문 대신 분류 |
| 결과·시간 | outcome, elapsed_ms, observed_at, parent_event_id. 단조 시계 duration, pending/failed/cancelled/budget_exhausted/candidate_only 구분 |
| 기록·재사용 | recording_status, validation_state, retention_expires_at, source_rights_state. unknown/not_run을 성공으로 바꾸지 않음 |

enum/길이/버전 호환은 R01 PR에서 고정한다. hash는 권한 증명이 아니다.
최종 결과에는 실행 전 거부/취소/예산 종료/부분 실패도 구분한다. 테스트와 독립 평가 통과는
별도다. resource_usage는 실제 측정만, 미측정은 null이다. artifact도 동의/tenant/보존을 재확인한다.

## 저장·장애 처리와 Coding 연결

1. R01 strict 계약·sanitizer·adapter·no-op/memory sink부터 검증한다. 모델/추가 네트워크 호출은 없다.
2. R02는 StrategyEvent/Experience 저장 구조를 별도 제안한다. 기존 SQLite의 독립 테이블/
   세션/트랜잭션을 사용하며 migration은 별도 승인 경로다. 기존 memory/training/Worker 테이블에
   임의 전략 JSON을 끼워 넣지 않는다. 새 schema의 기존 서비스 기동 영향도 먼저 검증한다.
3. bounded enqueue·유한 DB 대기·멱등 unique key·quota/보존을 둔다. 무제한 재시도/큐·장애 우회용
   원문 로그 쓰기는 금지한다. 프로세스 종료 전 미저장 이벤트 유실 가능성을 숨기지 않는다.
4. 실패 시 recording_status=unavailable/gap으로 표시하고 코드 결과·승인을 바꾸지 않는다.
   저장 확인 없는 경험은 검색/최적화/학습에 사용하지 않는다. 필수 보안 승인 실패는 계속 거부한다.
5. 재시작은 확인 sequence와 중복을 조정한다. unknown 구간을 모델로 채우거나 미실행 이력을 만들지 않는다.
   가능한 구간은 재전송+멱등으로 중복을 억제하되 장애 중 정확히 한 번/무손실을 보장한다고 말하지 않는다.

prepare/승인 대기, patch 검증/적용/실패, runner 시작/종료, 복구 attempt, 회귀 결과,
취소/예산 종료, PR 후보 결론에 최소 이벤트를 연결한다. strategy_id는 서버 등록된 버전 참조다.
모델 자유 문자열만으로 우수 전략을 등록하지 않는다. 초기에는 기록의 존재가 prompt/모델/
권한/실행 분기를 바꾸지 않아야 한다. C01부터 실제 runner ID/hash, C02부터 복구 관계를 기록한다.
기존 Workspace 단독 동작을 전체 Coding Agent 완료로 재분류하지 않는다.

## Retriever 검증

영속 저장 → 증거/출처 검토 → 재사용 가능한 관찰 → 범위/버전/만료 필터 → 제한 검색 →
적용 조건/주의점/근거 제안 순서다. test_result=passed만으로 검증하지 않는다. 재현 가능한
실패 관찰도 검토 후 검색할 수 있으나 일반화 우세/학습 적격과는 다르다.
초기 검색은 CPU의 category·언어/프레임워크·버전 등 최소 특징부터 검증한다. 새 임베딩 모델/
외부 API 설치를 선행 조건으로 만들지 않는다. stale/삭제/철회/다른 tenant/숨김 평가 경험은
후보 생성 전에 제외한다. 기록의 명령은 비신뢰 데이터다. 검색 장애·빈 결과·비활성화 시
기존 코딩 경로로 복귀하며 권한 확대/명령 실행을 하지 않는다.
S02는 신규 repo/문제군에서 같은 모델/도구/예산으로 off/on을 비교한다. 성공/회귀/총시간/
검색 p50/p95/자원/불확실성을 함께 평가한다. 표본/최소 실익/허용 비용을 사전 고정하고
실익 미확인·중대한 회귀면 비활성 유지한다. Preview 피드백은 동의한 gap 분류·수정/검증 ID만
기록하고 사용자 코멘트 원문은 기본 제외한다.

## Evaluation Agent와 Optimizer

E01은 역할 정의와 별도로 실제 작업 실행·독립 테스트·회귀/안전 판정·집계 보고서를 생성해야 한다.
정답/숨김 최종셋은 개발 에이전트와 분리하며 평가 서비스 오류는 unknown이다.
O01은 같은 문제/base SHA/model/prompt/tools/budget에서 전략 버전만 바꿔 통제 비교한다.
문제 난이도/캐시/실행 순서 효과를 기록하고 배정/seed/반복/탈락 기준을 사전에 고정한다.
성공 외에 회귀/보안/지연/CPU/RAM/VRAM/실패·취소/구간 추정을 본다. 3~5회 반복은 예시이며
통계적 충분성이나 독립 사례 다양성을 보장하지 않는다. 후보/기각/inconclusive를 보존하고
승인 전 활성 전략을 바꾸지 않는다. O02에서 부분 반영/전체 도입/보류/폐기를 Evolution Lab과
연결하고 즉시 비활성·이전 버전 복귀를 검증한다.

## 선택적 모델 학습

경험 → 증거 검증 → 동의/사용권·라이선스 → PII/사내정보/비밀 → 품질/중복 → repo/문제
계열 split·누출 검사 → 버전/hash manifest → 승인된 후보 export 순서다.
실패 기록은 보존하지만 실패 응답을 정답 completion으로 자동 변환하지 않는다. 교정 행동을
학습시키려면 정답성도 검증한다. 개발/검증/숨김 최종셋을 분리하고 최종셋 문제/정답/해설/
파생 요약의 검색·학습 export를 금지한다. 철회/삭제된 경험은 미사용 후보를 무효화한다.
이미 학습한 모델에서 해당 정보가 자동 제거됐다고 주장하지 않는다.
L01은 candidate_only export이며 GPU 작업을 예약하지 않는다. L02에서 별도 승인 후 작은
모델/데이터로 기존 학습/평가/롤백을 재사용한다. 모델/어댑터/data/eval hash·예산·실패를 기록하고
비학습 repo/문제군에서 비교한다. 개선 미확인/악화면 폐기하고 운영 모델을 유지한다.
모델 활성화/운영 적용은 다시 별도 승인이다.

## 테스트 기준 제안

아래는 미작성·미실행 계획이다. 구현 시 기존 Excel QA에 요구사항 ID·selector를 연결한다.

| ID | 시나리오 | 통과 기준 |
|---|---|---|
| STR-01 | schema/unknown/버전/enum/길이/비밀·PII canary | DB/이벤트/오류/인덱스/export 원문 유출 0, 비정형 문자열 자동 저장 금지 |
| STR-02 | 타 user/project/task/artifact·CSRF/미인증/비소유자 | 미인가 읽기/쓰기/삭제/export 0, 서버 actor에서 소유권 결정 |
| STR-03 | 중복·상이 payload·순서 역전·crash/restart | 중복 통계/충돌 덮어쓰기 없음, gap/unknown 명시·허위 복원 없음 |
| STR-04 | lock/full/없는 schema/sink 예외/큐 포화 | 유한 대기, 코딩 결과/승인 불변, raw fallback 저장 없음 |
| STR-05 | prepare/apply/runner/복구/취소/예산 종료·부분 실패 | 실제 attempt/상태와 기록 일치, 미실행/미측정의 성공/0 변환 없음 |
| STR-06 | Recorder off/on·장애 비교 | 모델 호출/도구/patch/권한 결과 불변, 외부 호출·학습 0, 기록 비용 실측 |
| STR-07 | v3 fixture/migration·backup/restore | 기존 데이터/세션/외래키 보존, 자동 destructive downgrade 없음 |
| STR-08 | 만료/철회/삭제/stale/미검증/타 tenant | 검색/캐시/미사용 학습 후보 제외, 검증된 실패는 근거와 조회 가능 |
| STR-09 | 숨김 평가 canary·문제 계열 중복·주입 전략 | 검색/prompt/train export 누출 0, 명령·권한 실행 0, 정답 비공개 |
| STR-10 | Retriever off/on 신규 작업군 | 사전 기준의 실익/불확실성/회귀/시간/자원 보고, 미충족 시 비활성 |
| STR-11 | 통제 비교·평가 장애·작은 표본·승격/rollback | 조건 불일치 거부, 장애 unknown·부족 inconclusive, 미승인 승격 차단 |
| STR-12 | 권리 불명/비밀/미검증/철회/누출 export | 부적격 학습 후보 0, 심사 추적·manifest, export만으로 학습/교체 0 |
| STR-13 | Preview 피드백 동의/철회 | 미동의 원문 미저장, 최소 gap·수정 증거, 철회 후 검색/후보 무효화 |

공통 회귀는 auth/CSRF/OpenClaw tools.deny/DB migration·backup/workspace/runner다.
단위/임시 Git/실제 Docker/브라우저/실모델/실GPU 검증을 따로 보고한다.
