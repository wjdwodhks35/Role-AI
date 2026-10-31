# Phase 1 잔여 / Phase 2 진행 기록

승인 기준: 사용자 지시와 `/home/serveradmin/role-ai-worktrees/docs/test.md` (2026-10-30 KST).
단계별 **구현 → 테스트 → 전용 PR → CI·충돌·리뷰 확인 → dev 병합 → 다음 단계** 순서를 지킨다.
운영 DB/서비스/Compose/Render/Cloudflare/비밀키/새 모델/학습/유료 API/개인 데이터는 승인 밖이다.

## 복원한 기준

- 시작 원격 dev: `f542610ca9aeeeee345935e07710271decbf048e` (P1-01 #34, 로그 #35, P1-02 #36 포함).
- 로컬 `/home/serveradmin/Role-AI`의 dev는 `df65f4d`로 원격보다 뒤에 있고 `.codex-local/`가 있다. reset/checkout/정리를 하지 않았다.
- P1-02 원본 12개와 pr30 작업 변경은 보존한다. 승인된 단계마다 최신 원격 dev의 별도 worktree를 쓴다.
- 공유 문서 5개는 dev에 없다. PR #33 `1435db5e…`와 로컬 확정본을 참고하며 #33을 자동 병합하지 않는다.
- 확정 DEC-012~014/016과 최신 사용자 지시가 과거 미착수/미승인 기록보다 우선한다.
- 이번 지시는 각 단계의 조건부 dev 병합을 명시적으로 승인했다. 이는 이번 범위의 승인이고 운영 배포 승인이 아니다.
- 문서의 단계 구분에 따라 P1-03은 인증/자료 읽기 Adapter, P1-04는 문서·기억·검색, P1-05는 도구 승인 및 대화·견적·세션 경계를 모두 다룬다. 새 대화 고정 요구를 누락하지 않는다.
- 미병합 #23/#24/#26은 도구/지침/프로젝트 우선순위, #28은 Strategy, #31은 Coding 후보, #32는 Planning, #33은 공유 문서다. 이번 단계의 신규 `scope_permissions.py`와 직접 소스 충돌은 없다. 기존 PR을 통합/종료하지 않는다.

| 단계 | 상태 | 근거/다음 작업 |
|---|---|---|
| P1-03 | dev 병합 완료 | `feature/p1-03-scope-permission-gate`, 아래 검토 기록 |
| P1-04 | dev 병합 완료 | PR #38 / `67014f7a661f8d14e4ac03183bd2b6ff72a2b3c8` |
| P1-05 | dev 병합 완료 | PR #39 / `d4dbc2d9ca2ebe4382c61bf63212e2aed961509b` |
| P1-06 | dev 병합 완료 | PR #40 / `dc7fde777b24cc4fc147a4158861bc9b1dcefc4b` |
| P1-07 | dev 병합 완료 | PR #41 / `06900f1e52ca671896784c17ef856ac8c6a7f097` |
| P1-08 | dev 병합 완료 | PR #42 / `cb73dd2230c2c0fc451bb5fe0cbed8c965913431` |
| P2-A | dev 병합 완료 | PR #43 / `28629cd70aaf7672a227b7b9d4bf2877da80c9d0` |
| P2-B | 로컬 검증 완료·PR 준비 | 446 시나리오·864 PASS, 실제 grader CI 추가 |
| P2-C | 미착수 | 격리 로컬 위임 |
| P2-D | 미착수 | 계측 |
| P2-E | 미착수 | 동일 조건 비교/최종 보고 |

## P1-03 수용 기준과 경계

- 기존 `PermissionGate.evaluate()`의 외부 실행 기본 거부를 유지한다.
- `evaluate_scoped_read`/`ScopeReadGate`는 신뢰된 서버가 전달한 DB 인증 세션을 매번 확인한다. 계약의 user_id나 approval_reference는 인증 증거가 아니다.
- 세션 만료/삭제, 실제 사용자, 활성 프로젝트 소유권, 최신 ScopePolicy 세대, 선언된 읽기 도구·권한·자료, 정확한 DB 등록 버전을 검사한다.
- P1-02의 현재 원본/Grant/철회/파생 출처 적격성을 재사용한다. 개인 보관함 None은 프로젝트 전체 wildcard가 아니다.
- 이 단계의 실제 읽기 권한은 인증된 소유권 또는 DB의 명시적 버전 Grant다. Task별 수정/실행 승인은 P1-05에서 추가하며 현재 모두 거부한다.
- SQL 장애/잘못된 계약/미등록 자료/미커밋 변경은 고정된 사유로 거부한다. 원문/토큰/SQL 예외를 오류에 복사하지 않는다.
- 읽기 결정은 관측 결과이며 캐시된 capability가 아니다. 각 사용 직전에 재검사해야 한다. 요청 도중 철회에 대한 전달 경계/세션 세대는 후속 API 연결에서 처리한다.
- 기존 API, UI, P1-01 digest, P1-02 모델/이행, Coding 승인/파일/예산 제한은 변경하지 않는다.

### P1-03 10 Pass (자체 검토, 독립 모델 평가 아님)

| Pass | 발견/조치 | 잔여 위험 |
|---:|---|---|
| 1 요구 | 자료 Gate와 실행 Gate 혼동 → 읽기만 별도 Adapter, 실행 기본 거부 유지 | 도구/Task 승인 P1-05 |
| 2 모순 | 계약 user_id를 본인 증명으로 오인 → 실제 세션 사용자 비교 | HTTP 연결 시 기존 인증/CSRF 재사용 필수 |
| 3 사실 | 권한은 DB 현재 상태임 → P1-02 적격성 및 정책 세대 조회 | 운영 데이터 미검증 |
| 4 현실성 | 기존 worktree가 원격보다 뒤처짐 → 새 f542610 worktree | 기존 로컬 dev는 의도적으로 그대로 |
| 5 구조 | 두 번째 권한 규칙 구현 위험 → 원본·버전·출처 검증 재사용 | API 연결 P1-04/05 |
| 6 보안 | model_copy/가짜 승인으로 확대 가능 → JSON 재검증·등록 자료 대조 | 인증 세션 hash는 서버 내부 전용 |
| 7 자원 | 출처 폭증 → 기존 64 깊이/출처·256 노드 제한 재사용 | 대량 실자료 성능 미측정 |
| 8 실패 | DB 오류/세션 삭제/다른 세션 철회 → 고정 오류 및 fail-closed 테스트 | 진행 중 요청 취소는 후속 |
| 9 반례 | 개인 보관함을 공통 검색으로 오인 → 명시 개인/프로젝트 거부 사례 테스트 | 과거 API는 아직 격리 미연결 |
| 10 통합 | Gate가 flush로 미승인 쓰기를 확정할 위험 → pending Session 거부 및 무변경 검증 | 호출자는 트랜잭션 경계를 관리해야 함 |

테스트·PR·병합 SHA는 확인된 결과만 추가한다. 미병합 단계는 완료로 표시하지 않는다.

### P1-03 실제 검증

- 관련 Gate/Scope/Task Contract 138 passed (최종 두 사례 추가 전).
- 최종 전체 Excel QA: 348개 시나리오·713회 PASS, ordered=true. 신규 Gate 9개 시나리오·25회 포함.
- 임시 복사본 `/tmp/role-ai-p103-verified-rywnra1t`의 Python/QA SHA-256이 최종 코드와 일치.
- 합성 DB/Mock·비밀 설정 없는 환경, 실제 socket 및 Docker/Ollama/GPU 명령 차단.
- Ruff·diff 검사 통과. 기존 Starlette/httpx deprecation 경고 1건. 실모델·운영 데이터 검증 아님.
- 변경 파일: `role_ai/permissions.py`, `role_ai/scope_permissions.py`, `tests/test_scope_permissions.py`, `qa/update_foundation_plan.py`, `qa/Role-AI_unit_tests.xlsx`, 이 기록.
- PR/CI/병합 정보는 실제 확인 후 기록한다. P1-04 이후는 아직 미착수다.

PR 준비 후 QA 요구사항커버리지에 REQ-SCOPE-PERMISSION 행 누락을 보완했다. 348개 테스트 선택자는 동일하고 앱/테스트 코드는 변경하지 않았다. 이 QA 메타데이터 변경 뒤의 최종 head는 GitHub 전체 CI로 재검증한다.

P1-03 병합 확인: PR #37, dev `89f61c4502e702b7dcf527d665ade198909f41c5`, 최종 CI 6개 성공. 병합 완료 근거는 PR의 완료 기록에 남겼다. P1-04의 기존 DELETE와 이력 보존 충돌은 P1-04_REVIEW.md에 기록했고 사용자가 1번 보관 처리로 확정했다. 현재는 UI 시안 승인 및 최종 PR/CI/병합을 기다리며 P1-05 이후 구현은 하지 않는다.

2026-10-30 P1-04 갱신: 사용자가 삭제 1번 보관 처리를 확정했다. v5 추가형 경계·보관, 자료/대화 경로 구현 및 전체 QA 366개 시나리오·732회 PASS. 기존 UI 안내가 정책과 달라 최소 문구/프로젝트 연결 시안을 제시했다. static 코드는 아직 미변경이며 UI 승인·최종 CI·PR/dev 병합 전까지 이 단계는 미완료다.

P1-04 UI 승인 적용 및 최종 검증: 시안대로 문구/프로젝트 요청만 연결. 전체 QA 367개 시나리오·733회 PASS, 클라이언트/기존 Node 검사 통과. P1-04 PR/CI/dev 병합 확인 후 P1-05로 진행한다. 추가 요청은 REQUESTS.md에 기록하고 승인 범위의 독립 작업은 계속한다.

### P1-04 병합 수신 기록

- PR: https://github.com/wjdwodhks35/Role-AI/pull/38
- 최종 로컬 QA: 367 시나리오·733회 통과. 기존 인증/IME 및 승인된 범위 클라이언트 검사 통과.
- CI 41015972243(Phase 1 4 jobs), 41015972133(log wrapper Ubuntu/Windows): 6개 전체 통과.
- dev 병합: `67014f7a661f8d14e4ac03183bd2b6ff72a2b3c8`. 원격 dev와 트리 확인 후 P1-05 시작.
- 운영 반영 없음. 기존 대화 보존·새 대화 정책 고정, 보관 해제/영구 삭제 UI는 후속 승인 사항.

### P1-05 검증 범위

현재 별도 worktree에서 유효 Task 승인 증거/v6·제한 도구 dispatcher·프로젝트/정책 세대 세션 키를 구현했다.
요청 사항은 REQUESTS.md로 분리한다. P1-06 구현은 이 단계의 CI 및 dev 병합 후에만 시작한다.

### P1-05 병합 수신 기록

- https://github.com/wjdwodhks35/Role-AI/pull/39, 최종 head `f89b46dd8fa58c6328a3164f93771e8251185b98`.
- 합성 QA 385 시나리오·767회 PASS. 최초 CI import 결함을 수정하고 역할 capability 및 WAL snapshot/flush 보존 반례를 보강했다.
- 최종 CI 41019888521(4 jobs), 41019888511(Ubuntu/Windows 2 jobs) 모두 통과. 충돌·미해결 리뷰 없음.
- dev merge `d4dbc2d9ca2ebe4382c61bf63212e2aed961509b`. 원격 tree `663af76107aeae2cadcb620e130865fe01347eba`와 로컬 검증 트리 일치.
- 운영 v6 이행/배포 없음. 일반 외부 도구 기본 거부 유지. 이 병합 확인 후 P1-06 시작.

### P1-06 병합 수신 기록

- https://github.com/wjdwodhks35/Role-AI/pull/40, 최종 head `547ad6eae6b252f1b22dc7fdc5a23295609d1f19`.
- 합성 QA 402 시나리오·791회 PASS. 현재 승인, 상태 CAS, 실제 파일 잠금, 재시작 명시 재개, 변조/저장 실패/제어를 확인.
- CI 41021499663(4 jobs), 41021499687(Ubuntu/Windows 2 jobs) 전부 성공, 변경10개 파일/충돌·미해결 리뷰 없음.
- dev merge `dc7fde777b24cc4fc147a4158861bc9b1dcefc4b`, 원격 tree `8944bff421e33abb4263bbb49efe077be5676a7e`와 검증 트리 일치. 확인 후 P1-07 시작.
- DB v6 그대로, 운영 적용 없음. 일반 외부 실행 기본 거부 유지.

### P1-07 병합 수신 기록

- https://github.com/wjdwodhks35/Role-AI/pull/41, 최종 head `5ac54adbd357bbb7ab4a5af5b6377bb39138d448`.
- 합성 QA 417 시나리오·813회 PASS, 실제 토큰/VRAM 계측이 아닌 fixture 검증.
- CI 41023457807(4 jobs), 41023457882(Ubuntu/Windows 2 jobs) 모두 성공. 변경8개 파일, 충돌·미해결 리뷰 없음.
- dev merge `06900f1e52ca671896784c17ef856ac8c6a7f097`, 원격 tree `08983ea92ca6de7c2964e00a7ef27c82900b242f`와 검증 트리 일치. 확인 후 P1-08 시작.
- 운영 설정/DB/모델 실행 없음. GPU admission과 물리 hard cap을 구분한다.

### P1-08 / Phase 1 완료 수신

- https://github.com/wjdwodhks35/Role-AI/pull/42, head `9835e619adeac52b255fec38b951ba23ce306c34`, dev merge `cb73dd2230c2c0fc451bb5fe0cbed8c965913431`.
- 최종 합성430 시나리오·830회 PASS, CI41026173839(4 jobs)/41026173818(2 jobs) 모두 성공. 변경10개 파일/충돌·미해결 리뷰 없음.
- 원격 tree `9ea5afc51aab43f05ae51d630728db28f0811d04` 일치. P1-01 #34/P1-02 #36 조상 포함 확인.
- Phase 1 코드·합성 보안/복구 통합 완료. 실제 LLM 성능/운영 배포·이행은 포함하지 않음. 완료 보고서는 외부 `PHASE1_COMPLETION_cb73dd22.md`에 실제 SHA와 한계를 기록했다.
- 이후 이 merge에서 P2-A 시작. 공유 PR33/미병합31/32 등은 자동 통합하지 않음.

### P2-A 병합 수신

- https://github.com/wjdwodhks35/Role-AI/pull/43, head `e9d7e61f21afd9347b770766dcfe23b9dccc3aa7`, dev merge `28629cd70aaf7672a227b7b9d4bf2877da80c9d0`.
- 합성436 시나리오·838회 PASS. CI41028247987(4 jobs)/41028247991(2 jobs) 모두 성공, 변경12개 파일/충돌·미해결 리뷰 없음.
- 원격 tree `8b5550793ac99e1c5de5a7bf92ffc975931c1476`와 로컬 일치. PR30 기존 기반 유지, PR31 오류/체크포인트 일부만 이식. 원본31 미병합 보존.
- 실제 LLM/성능/토큰·GPU 미측정. 확인 후 P2-B 시작.

## P2-C — 공통 계약을 통한 기존 Coding Agent 위임

- 시작 dev `bba1ba1df1a1c7aead21afc1a354978494ad8916` (#44). 독립 feature/p2-c-local-delegation.
- common live gate/실행 상태/취소/예산 ledger와 기존 CodingAgent/CodingWorkspace/public Docker runner/독립 hidden grader 연결. 원본 repo에 자동 적용하지 않는다.
- 선택적 고정 Windows localhost transport, 설치 모델 digest 바인딩, 신규 소스 해시 승인 무효화. 기존 UI/API/권한 기본값/Task Contract digest 변경 없음.
- 관련 격리222개 통과. 최종 소스 전체 QA457시나리오/884실행 통과(162.74s, snapshot ad34q9cr; CI는 PR에 기록). Ruff 통과, QA 갱신 두 번째0행.
- 실제 Windows Ollama metadata만 확인. 로컬 Docker 접근 부족으로 전체 실모델 성능은 미측정, Codex tokens/VRAM peak null. 사전검사 blocked/exit2/model_calls0. 추가 조치는 REQUESTS에 기록.
- 실제 public+hidden Docker 연결 검사는 기존 GitHub CI job에 추가(scripted model, LLM 성능 아님). 단계 PR/CI/병합 수신은 PR 본문·외부 재개 상태 파일에 기록하며 확인 전 완료 처리하지 않는다.
- 10 Pass: P2-C_REVIEW.md. 다음 P2-D 구현은 P2-C dev 병합 확인 후에만 시작.

## P2-C 병합 수신

PR #45 https://github.com/wjdwodhks35/Role-AI/pull/45, CI41073430341(4)/41073430533(2) 모두 success, 실제 scripted model/public+hidden Docker 연결 PASS. dev merge `1e340ab5cdea7c37c9fef90a6fc8344f14114428`, tree8b1f8d1a… 확인. 실모델 성능은 미측정이다.

## P2-D — 증거 기반 성능 계측

시작 dev1e340ab5… (#45), feature/p2-d-evaluation-metrics. strict 측정 기록/실제·합성 구분/실패 분모/미측정null/장치 VRAM observer 및 private 원문·중복 실행 보존. 관련48개 통과, 실제 GPU observer3샘플·오류0·관측1787MiB(model_calls0, 모델 성능 아님). 전체 QA465시나리오/900실행 통과(snapshot hz7wtce0, app/tests 해시 일치), Ruff/diff 및 QA 재갱신0행. 실제 Docker용 QA checker는 이후 측정 변환을 연결했으므로 최종 CI에서 추가 검증한다. CI·병합 확인 전 완료 처리하지 않는다. 10 Pass는 P2-D_REVIEW.md, 운영 변경 없음.

## P2-D 병합 수신 및 실제 구성 요소 검사

PR #46 https://github.com/wjdwodhks35/Role-AI/pull/46, CI41075190144(4)/41075190142(2) 모두 success. dev merge `6ec1c88707589d1bcad6f0fd7a7cb1d7437cc22e`, tree5c5b9c62… 확인. 실제 read-only 모델 계획1회 backend87+32 tokens/9.261871s/장치peak5568MiB(18샘플), 종료resident0. 독립 코딩 채점이 없어 common interrupted·전체 품질 점수 없음.

## P2-E — 비교/공개 기준선·독립 채점 구현, 실제 A/B 미완료

시작 dev6ec1c887… (#46), feature/p2-e-comparison-report. 동일10과제/seed·조건·구현·반복 수 검증, 품질 회귀/미측정/공정성·CI 근거 부족 시 개선 차단. 공개 패키지와 합성 DB 독립 baseline grader 준비, 미검증 작성 주체는 실제 집계 제외. 관련45개 통과 후 추가 CLI 회귀/제공자 수신 테스트 포함 최종 전체 QA479시나리오/922실행 통과(snapshot gmqodo8e, app/qa/tests SHA256 일치). Ruff/diff 통과. 실제 비교 A0/B0/exit2로 미완료 확인.

필요한 요청은 REQUESTS.md, 실제 진행/수치/미측정은 PERFORMANCE_REPORT.md, 10 Pass는 P2-E_REVIEW.md. 실제 A/B 및 Docker 접근/공정성 근거가 없어 P2-E는 Draft PR/미병합 상태로 보존한다. CI green만으로 전체 목표 완료 처리하지 않는다.

## P2-E 추천안 후속 구현 (2026-10-31 KST)

기존 Draft #47/head ae1012721…에서 사용자 승인 추천안을 진행했다. A/B 명시적 private rootless Unix socket 선택, inode·소유권 live 승인 바인딩, 기존 Docker resource/image preflight 재사용을 추가했다. 관련80개 통과 후 malformed info 거부를 보강한 최종 전체 QA486시나리오/952실행 통과(snapshot _qkvn9fx, app/QA/tests SHA256 일치), Ruff/diff 통과. Rootless 실제 실행은 아직 미검증이며 uidmap 관리자 준비가 필요하다. 상세 준비안은 ROOTLESS_SETUP_PLAN.md, 공식 usage 근거/수집 공백은 CODEX_USAGE_PROTOCOL.md, 새 공개 패키지·남은 조건은 REQUESTS 최신 절을 참고한다. 새 코드/QA/CI 검증 후 같은 P2-E Draft PR에 반영하며 실측 근거 부족 상태에서는 병합하지 않는다.

## P2-E 실제 환경 및 B10 실행 수신 (2026-10-31 KST)

사용자 uidmap 설치 확인 후 private Rootless daemon에서 kernel 자원 제한·기존 CodeExecutor·Coding runtime·독립 grader·위임 통합을 검증했다. QA synthetic fixture가 env socket을 덮어쓰는 문제만 명시 전달로 수정했다. core 구현 digest f028…4061은 그대로다. 전체 QA486/952는 이전 검증이며 이번에 반복 실행했다고 표현하지 않는다.

고정 B10시도:4성공/3최종 채점 실패/2예산 제한/1모델 전 승인 차단. 채점7건39/42, 미채점18/60사례. 측정 backend 소계40,590(9건)/56호출, 외부 supervisor362.933235s, 장치 peak5447MiB. 실제 clock 역행으로 승인 fail-closed한 기록을 보존했고 권한/시계를 바꾸지 않았다. A0/Codex usage null/절감률 null이며 PR #47 Draft·미병합 유지. 최신 세부 수치/요청은 PERFORMANCE_REPORT/REQUESTS의 현재 절을 우선한다. 새 head CI 수신은 PR/외부 상태에 기록한다.

## P2-E 독립 A 승인·실측 비교 완료

사용자의 추가 독립 Codex 에이전트 명시 승인으로 fresh CLI10프로세스에서 공개 목표/stub/test만 전달했다. 모델 도구 실행0·이전 대화/oracle 전달0·후보 host 실행0. 동일 답안 교정 채점 A10/10·60/60, B4/10·39/42(7건 채점), A/B20기록 coverage=true/비교기regression/improvement=false/절감률null. A usage 생성 input130510/output2261 및 cached/reasoning 별도 category 원문을 보존했다.

첫 A채점10건은 umask077 상속 때문에 runner.py 접근 거부로 모두 interrupted였다. 모델을 재호출하지 않고 worker-local022(B와 동일)에서 같은 hash 후보만 재채점했으며 초기 실패/26.642454s를 별도 보존했다. A 생성86.083067s/유효채점28.717817s, B supervisor362.933235s. provider 예산/모델ID·계측 범위/순차실험 한계를 명시했다.

최종 변경은 문서와 실측 수신뿐이다. 기존 코드 기능 회귀/보안/6CI·리뷰·충돌 확인 후 평가 시스템과 부정적 결과를 기록하는 PR47을 dev에 반영한다. 로컬위임 개선/운영적용 또는 토큰절감 검증으로 해석하지 않는다. 최종 head/CI/merge SHA는 PR 본문·외부 PHASE2_COMPLETION.md와 재개 상태에 기록한다. Phase3/운영 변경은 별도 범위다.

## R1 — P2-E 후속 umask 호환성

시작 dev8511e001…(#47). 사용자 “남은 작업 진행”에 따라 별도fix/runner-restrictive-umask에서 기존runner의엄격umask 읽기오류를 수정했다. work0755명시/호스트부모0700/파일0444·기존sandbox/승인/자원유지. 신규단위2개는수정전실패,수정후관련167개PASS. 실제privateRootless의umask077·기존격리/OOM/timeout/실패/cleanup검사17.392s/exit0. QAselector1행추가·기존행보존. 모델실행0/운영변경0. 전체QA·CI·PR·merge수신은확인후기록하며10Pass는FOLLOWUP_UMASK_REVIEW.md. 새core digest가되므로이전A/B점수를새소스성능으로주장하지않는다.

R1최종로컬수신: 전체QA487시나리오/954실행PASS, snapshot1kbumvde와app/tests/QA해시일치. Ruff/diff통과. 소스변경에대한실제CI/PR·병합수신은확인후별도기록한다.

## R1 병합 및 R2 usage 증거 보강

R1 PR48 dev2689dc6e8cad3856f00993a22a782323834daf17 병합 확인. 로컬487/954·CI41207833892(4)/41207833955(2)전부success, 실제rootful/rootless restrictive-umask077 PASS, tree8d3df31c…일치/충돌·미해결리뷰없음.

R2는이dev에서feature/codex-usage-evidence. offline JSONL reader/원문해시·종료코드·구간수/미측정null·범주보존·중복/잘림/경로위협거부를추가. 앱/권한/계약변경없음. 기존A10원문소계130510input/2261output재현,정의/검토부재로완전합계null/exit2. 신규모델호출0/운영변경0. docs/CODEX_USAGE_EVIDENCE.md 및 FOLLOWUP_USAGE_REVIEW.md에사용·범위·10Pass기록. 최종CI/PR/merge는검증후수신으로기록.
