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
