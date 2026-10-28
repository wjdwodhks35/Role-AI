# Role-AI 1.0 v1.1 GAP 분석

분석일: 2026-10-28. 제품 기준은 사용자 제공
`ROLE_AI_1_0_CODEX_IMPLEMENTATION_PLAN_v1_1.md`이며 이전 v1.0 계획보다 우선한다.
착수 기준 dev는 `d3d790a6a604374e9c2ec26d8425657d19eddd6c`다.
v1.0/v1.1 원본 기획서는 내용 변경 없이 docs에 보관한다. 최신 제품 기준은 v1.1이다.

## 현재 Coding Agent 구현 · PR #29

현재 개발 범위는 Coding Agent 실동작 완성에 한정한다. 다음 변경은
[PR #29](https://github.com/wjdwodhks35/Role-AI/pull/29)에 포함하며, 운영 배포를 의미하지 않는다.

| 기능 | 구현 상태 / 근거 | 남은 확인 |
|---|---|---|
| 탐색·반복 수정 | coding_project.py, coding_workspace.py; 분할 읽기·검색·정확한 부분 교체·누적 diff·바이너리 보존 | 큰 저장소는 현재 파일/용량 상한 밖 |
| 격리 명령 실행 | coding_runner.py; pinned Docker의 unittest/pytest 템플릿, shell·host fallback 없음 | pytest용 의존성 이미지는 별도 준비 필요 |
| 계획→수정→테스트→복구 | coding_agent.py; 현재 revision의 통과 증거, 오류/순환/예산 종료 | 실제 설치 모델의 코딩 품질 미검증 |
| 체크포인트·중단/재개 | coding_jobs.py; 원자적 파일 저장·소유 잠금·예산 유지·unknown 확인 | 운영 장애/실사용 장시간 검증은 별도 |
| 최소 Strategy 기록 | strategy_events.py의 계약과 선택적 runtime metadata | 검색·최적화·학습·DB 전용 저장은 미연결 |
| 작업 API·화면 | coding_api.py, static/coding.js; 기존 auth/CSRF 재사용 | 실브라우저 수동 검증 대기 |

구현 커밋 `00ee826` 기준 CI 273개 시나리오·523회 실행과 실제 Docker 복구/재개 검사가
통과했다. 이는 실모델·실GPU 성능 검증과 다르다. 상세 사용·예산·보안 경계는
[runtime 안내](../coding-agent-runtime.md)를 따른다.
Preview Lab·멀티모달·Retriever·Optimizer·파인튜닝·AI Office 등은 이번 단계에서 구현하지 않는다.

## 착수 전 dev 기능 매트릭스 · 역사적 기준선

아래 표는 착수 전 dev 기준이다. Coding 관련 현재 변경은 위 표를 우선한다.
구현됨은 소스 확인 기준이며 실환경 검증과 구분한다.

| 대상 | 상태 | 실제 근거 / 차이 |
|---|---|---|
| API·인증·CSRF·SQLite | 구현됨 / 운영 미검증 | main.py, security.py, migrations.py; schema v3. 운영 DB 내용은 읽지 않음 |
| 기억 | 부분 구현 | memory.py, models.py; 후보/확인/정정/삭제/만료/이력. 개인화 품질 실측 필요 |
| OpenClaw 대화 | 구현됨 / 실환경 미검증 | assistant.py; 소유자·loopback·token·로컬 모델·도구 전체 차단. Windows Gateway는 별도 |
| 6개 역할 | 정의만 구현 | agent_registry.py; execution_enabled=false, scheduler_enforced=false |
| Coding 작업 계약 | 구현됨 | coding_tasks.py; task/user/project/repository/commit/allowed_files/예산·승인 결합 해시. 해시는 인증 수단이 아님 |
| 격리 파일 수정 | 구현됨 / 임시 Git 검증 이력 있음 | coding_workspace.py; worktree prepare/apply/close, 생성/수정/삭제·diff·원본 dirty 보존 |
| Coding 상태 이벤트 | 부분 구현 | CodingWorkspace.event()는 dict 반환만 함. 영속 기록·전략·attempt 이력 없음 |
| 테스트 실행 | 제한적 구현 | code_execution.py; Python submission+unittest 전용. 저장소 runner·Workspace 연결 없음 |
| Coding Agent 전체 루프 | 미구현 | 승인 API/작업 영속성/모델 계획/테스트/실패 복구/PR 후보 미연결 |
| Strategy Recorder | 미구현 | 작업 유형·전략·attempt·테스트/오류·시간·결과를 묶는 계약·저장·연결 없음 |
| Strategy Retriever | 미구현 | 검증·만료·리비전 필터/유사 경험 검색/근거 제안/미사용 기준선 비교 없음 |
| Strategy Optimizer | 미구현 | 전략 버전·통제 비교·독립 작업셋·승격/거부/롤백 없음 |
| Evaluation Agent | 역할 정의만 구현 | 요구사항 고정 정답 평가·quality exact match·RLAIF 학습 평가는 전략 독립 평가를 대신하지 않음 |
| 전략 경험 모델 학습 | 미구현 | 기존 SFT/QLoRA·RLAIF/GRPO 기반은 있으나 경험→권리/PII/누출 심사→학습 후보 연결 없음 |
| Preview Lab / Intent Gap | 미구현 | request_analysis preview는 분석/역할 추천이며 작동 시제품이 아님 |
| PDF·이미지·영상 | 부분 구현 | PDF 텍스트/RAG만 존재. OCR/레이아웃/VLM/핵심 프레임/시각 QA 없음 |
| 수집·지식 | 부분 구현 | HF JSONL 제한 수집·정제·해시·RAG. 다중 소스/계보/유사 중복/Hybrid/캐시 보강 필요 |
| Adaptive Runtime | 부분 구현 | GPU admission/공유 잠금, training/collection Worker. 중앙 P0~P4 큐·스트리밍 없음 |
| AI Office | 미구현 | 실제 역할별 작업 이벤트 연결 후 최종 UI 단계 |

`ai_judge.RewardRecorder`는 GRPO rollout/reward 기록기이며 Coding Strategy Recorder가 아니다.
`artifacts.normalize`의 prompt 단위 중복/분할과 제한된 PII 정규식도 전략 경험의 권리·사내정보·
저장소/문제 계열 단위 누출 방지 심사를 대신하지 않는다.

## 초기 통합 계획 · 후속 기능은 현재 집행 대상 아님

1. #27의 작업 계약·격리 파일 수정 기반은 재사용한다. Coding Agent 전체 완료로 표시하지 않는다.
2. 다음 개발 묶음의 앞에 Recorder 계약/저장을 배치하고 runner·복구 개발과 함께 성공/실패/
   미실행을 기록한다. 과거 경험을 모델 추정으로 소급 작성하지 않는다.
3. Retriever는 MVP 안정화·검증 경험·독립 비교를 통과한 뒤 Preview Lab 시점에 조건부 도입한다.
   경험이 부족하면 검색 연결만 보류하고 Preview Lab 개발은 진행한다.
4. 실제 Coding Evaluation Agent를 별도 PR로 완성한 뒤 Optimizer를 연결한다.
5. 파인튜닝은 자가학습 후반이다. 데이터 후보 생성, GPU 학습, 모델 활성화 승인을 분리한다.

## 우선 위험

| 위험 | 대응 |
|---|---|
| 기록 장애가 코딩/승인 결과에 영향 | 별도 트랜잭션·유한 대기/큐·gap 표시. 실행 권한 검사는 계속 fail-closed |
| 테스트 1회 통과를 우수 전략으로 승격 | 관찰/검증/일반화 평가/학습 적격 상태 분리; candidate_only 기본 |
| 요약·도구 로그의 비밀/사내정보 유출 | 초기 enum/ID/계수만 허용, summary=null, 원문/args/diff 저장 금지 |
| 타 사용자·숨김 평가셋 회수 | 검색 전에 tenant/프로젝트/리비전/동의·만료 필터, 최종셋 검색·학습 경로 제외 |
| 새 schema 코드가 v3 서비스 기동을 막음 | migration·운영 적용 승인 분리. flag-off만으로 구 DB/구버전 호환이 생긴다고 가정하지 않음 |
| 기존 CI로 전략 성능을 과장 | 기능·실모델·GPU·일반화 검증 분리, 동일 조건 기준선과 비교 |

세부 순서는 [PR 계획](ROLE_AI_1_0_PR_PLAN.md), 경험 계약과 활성화는
[Strategy Learner 도입안](CODING_STRATEGY_LEARNER_ROLLOUT.md)을 따른다.
