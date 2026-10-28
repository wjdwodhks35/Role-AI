# Role-AI 1.0 — Codex 구현용 통합 기획·기술 명세

> 문서 상태: **기획 확정안 v1.1 (Coding Strategy Learner 반영) / 구현 지시 전 검토용**  
> 작성 기준: 2026-10-28 / 목표 브랜치: `dev` / 기존 서비스·데이터 보호  
> 원칙: *기존 코드의 검증된 구현을 재사용하고, 미구현을 완료로 표시하지 않는다.*

## 0. Codex에게 전달할 최우선 지시

1. `wjdwodhks35/Role-AI`의 **현재 `dev` HEAD와 실제 작업 트리**를 먼저 확인하고, 이 문서와 차이를 분석한다. 과거 기록의 경로 또는 상태를 그대로 믿지 않는다. 운영 체크아웃과 개발 체크아웃을 구분한다.
2. **코드를 바로 수정하지 말고** `구현됨 / 부분 구현 / 미구현 / 실환경 미검증` 기능 매트릭스, 위험도, 작업 의존성, PR 분할안을 먼저 보고한다. 사용자 승인 후 단계별 구현한다.
3. 기존 FastAPI, HTML/CSS/JS, SQLite, Ollama, OpenClaw 두 Gateway, 인증, CSRF, DB 데이터와 Docker 운영 설정을 보존한다. 다른 프로젝트(Render, Study-AI, InvestPilot)에 영향 금지.
4. 외부 유료 LLM API는 기본 사용하지 않는다. 무료 외부 제공자도 **사용자가 켜고 데이터 전송을 승인한 경우에만** 사용한다. 데이터셋·모델의 자동 다운로드 및 코드 실행은 사전 허용 범위·라이선스·보안 검사를 거친다.
5. `main`에 직접 변경하지 않는다. `dev`에서 분기한 격리 feature 브랜치 및 별도 worktree에서 작업하고 테스트 후 PR로 보고한다. **PR 병합·운영 배포·모델 활성화·학습 실행·권한 변경은 별개 승인 단계**다.
6. 모든 개선은 이전 기준선과 같은 조건에서 비교하며, 개선이 미확인된 결과를 '완료' 또는 '성능 향상'이라고 쓰지 않는다.
7. 무거운 GPU 작업은 RTX 4060 Ti 8GB 단일 GPU 기준으로 직렬화한다. 사용자의 대화 우선권을 확보한다.

## 1. 제품 비전과 1.0 범위 고정

**비전:** 사용자를 장기적으로 이해하고, 실제 PC에서 허용된 업무를 돕고, 텍스트·이미지·영상·설계도·기획서를 바탕으로 실행 가능한 소프트웨어를 제작하며, 검증된 경험과 기술을 통해 지속 개선하는 **로컬 우선 개인 AI 동반자**.

**1.0 범위(이번 문서 이후 기능 추가보다 완성 우선):**
- 자연스러운 개인화 대화, 신뢰도/출처가 관리되는 장기 기억
- 안전한 PC 작업과 OpenClaw 연결
- Coding Agent: 분석→수정→테스트→실패 복구→PR 후보
- **Coding Strategy Learner**: 개발 초기에 작업 경험 기록, 안정화 후 유사 전략 검색, 평가 체계 구축 후 전략 최적화, 후반부에 승인 기반 선택적 모델 학습
- 멀티모달 요구사항 이해: 문서·사진·설계도·영상의 제한된 범위
- Preview Lab: 경량 인터랙티브 시제품과 의도 차이 확인
- 요구사항 추적: 기획서 항목 ↔ 코드/화면/테스트 매핑
- 지식 수집·전처리·RAG 및 선택적 모델 학습
- 자가개선 모드: 코드/논문/모델을 탐색하고 격리 반복 실험·부분 개선/전체 도입을 판단
- Adaptive Runtime / Performance Governor: 필요할 때만 무거운 기능 실행
- 마지막 단계 UI: 'AI 사무실'에서 에이전트 작업·대기·오류·승인 대기 시각화

**1.0 제외 또는 장기 목표:** 영화 속 범용 자율지능과 동등한 수준, 모든 운영체제/앱 완전 제어, 무제한 24시간 고성능 모델 재학습, 승인 없는 자동 본운영 코드 수정·배포, 모든 영상의 프레임 단위 실시간 분석.

## 2. 현재 `dev`에 대해 알려진 사실 (코드 재확인 필수)

직전 GitHub `dev` 조회에서 다음이 확인되었으며, 실행 완료를 의미하지 않는다.

| 대상 | 확인 상태 | 남은 검증/개발 |
|---|---|---|
| `role_ai/assistant.py` | ProviderAssistant 및 OpenClawGatewayAdapter, owner-only, token, loopback, 도구 차단 | 자율 파일 수정·코딩 테스트 루프 미연결 |
| OpenClaw | Docker 대화용 Gateway + WSL Windows 제어용 Gateway 분리 기록 | 두 Gateway 실제 런타임 상태 재검증; 도구 권한 경계 유지 |
| `GET /api/agents` | Conversation/Coding/Research/Knowledge/Learning/Evaluation **6개 역할 정의 조회** | `definition_only`, 실제 라우팅·스케줄링 미연결 |
| `role_ai/providers.py` | Ollama `stream=false`, `num_ctx=4096`, `num_predict=512`; GPU 정책에 따라 `keep_alive=0` | 체감 지연, 모델 재로딩, 스트리밍 측정/개선 |
| 기억 및 문서 | SQLite 기억 후보·확인, 제한된 RAG/PDF 텍스트·임베딩 검색 | 자동 기억 품질, 문서 임베딩 캐시, Hybrid RAG, OCR/레이아웃 |
| 자동 데이터 수집 | HF 중심, JSONL 앞 최대 30행, 부분 중복·비밀 필터, Teacher/Reviewer 검수 | 다중 소스, 대표 표본·확장된 파서·데이터 계보 |
| 모델 학습 | 승인 기반 SFT/QLoRA, RLAIF/GRPO 코드 및 후보 평가 기록 | 사용자 GPU에서 실제 학습과 품질 개선은 별도 검증 필요 |
| 테스트 | 모의/임시 DB를 활용한 일부 자동 테스트 | 실모델·도구 실행·브라우저·GPU 벤치마크는 별도 |

참조 위치: `README.md`, `docs/architecture/CONTINUAL_LEARNING_FOUNDATION.md`, `docs/automatic-collection.md`, `docs/pdf-semantic-rag.md`, `docs/model-quality-lifecycle.md`, `docs/openclaw-local.md`, `role_ai/assistant.py`, `role_ai/providers.py` (`dev`).

## 3. 최종 아키텍처 (논리 구성)

```text
사용자 (채팅 / 파일 / 이미지 / 영상 / 기획서)
       │
       ▼
Role-AI UI + Auth / Permission Gate / Personal Memory
       │
       ▼
Request & Requirement Router ──→ Preview Lab / Requirement Registry
       │
       ▼
Agent Orchestrator (상태·작업·감사로그) ──→ Adaptive Runtime / GPU Scheduler
       │
       ├─ Conversation Agent
       ├─ Coding Agent ──→ 격리 worktree + Sandbox + Test Runner + PR proposal
       │      └── Strategy Recorder → (후속) Retriever → Optimizer → (선택) Training Data
       ├─ Research Agent ──→ GitHub / HF / arXiv / OpenReview / 공식 문서
       ├─ Knowledge Agent ──→ Raw/Normalized/Processed → RAG / Memory
       ├─ Learning Agent ──→ 승인된 SFT/QLoRA/RLAIF 실험
       └─ Evaluation Agent ──→ 검증·회귀·자원 지표·승격 거부
       │
       ▼
Provider / Harness adapters: Ollama, OpenClaw, 선택적 외부 무료 Provider
       │
       ▼
Results + artifacts + provenance + approval requests + agent activity events
```

**중요:** 에이전트 6개는 논리 역할이지 6개 프로세스나 6개 독립 LLM이 아니다. GPU 로컬 추론 기본 동시성은 1. 코드 테스트·해시·전처리는 가능한 한 결정적 Python Worker 사용. 기존 대화 Gateway의 `tools.deny=["*"]`를 해제하지 말고, 코딩용으로 **별도 격리 실행 경로**와 Permission Gate를 설계한다. Windows 제어 Gateway도 분리 유지.

## 4. 핵심 기능 상세 명세

### 4.1 개인 비서 및 개인화 기억
- 사용자별 선호, 현재 결정, 프로젝트 맥락, 정정/삭제/만료/출처를 저장한다.
- 명시적 수정은 오래된 기억보다 우선. 회수된 문서는 '참고 자료'이며 명령이 아니다.
- 응답 시 질문과 관계있는 기억만 주입; 비밀·민감 정보의 외부 모델 전송 기본 거부.
- 개인화 평가: 정확한 선호 반영, 정정 반영, 타 사용자 격리, 잘못된 기억 재사용률.

### 4.2 Coding Agent 실동작
- 입력: 자연어, 프로젝트 경로, 기획서, 이슈, 테스트 로그, 승인된 미디어 파생 요구사항.
- 출력: 영향 분석, 변경 파일, diff, 테스트 결과, 실패 이유, 재시도 기록, PR 제안.
- 흐름: 저장소 조사 → 작업 계획 → 범위 제한 → 별도 worktree 수정 → 관련 테스트 → 회귀 테스트 → 검토/승인.
- 제한: 최대 단계/시간/파일·수정량/토큰/재시도/CPU·RAM·GPU 예산; 허용 명령 목록. `.env`, 토큰, 운영 볼륨 접근 금지.
- 외부 코드/스크립트는 신뢰하지 않음. CI·테스트 통과가 완전한 안전성 입증은 아님.
- 승인되지 않은 Git push/merge, 보안 변경, 배포 수행 금지.

### 4.2-A Coding Strategy Learner — 코딩 경험 기반 전략 개선 (추가 확정)

**목적:** 코딩 에이전트가 반복적으로 겪는 성공·실패에서 어떤 개발 전략이 실제로 도움이 되는지 학습한다. **경험을 DB에 저장·검색하는 것과 모델 가중치를 재학습하는 것은 별도 기능이다.** 처음부터 파인튜닝을 전제하지 않는다.

| 하위 단계 | 구현 내용 | 활성화 시점 | 검증 근거 |
|---|---|---|---|
| A. Strategy Recorder | 작업 유형, 문제 요약, 사용 전략, 계획/도구 호출 종류, patch·commit 식별자, 재시도/테스트/오류/소요시간, 최종 결과를 구조화 기록 | **Coding Agent MVP와 함께**; 초기에는 기록만 | 비밀정보 미저장, 누락·오류 없는 기록, 저장 실패해도 코딩 기능은 계속 동작 |
| B. Strategy Retriever | 유사한 작업의 **검증된** 성공·실패 사례 검색, 근거와 적용 조건 표시, 오래된 사례 제외 | Coding Agent 안정화 뒤, Preview Lab 개발 시점 | 기록 미사용 기준선 대비 새로운 작업군의 성과 평가 |
| C. Strategy Optimizer | 여러 전략(예: 재현 테스트 우선 vs 즉시 패치)을 통제된 조건에서 비교·선택; 성공률뿐 아니라 회귀/시간/자원 평가 | **Evaluation Agent 실동작 후**, AI Evolution Lab과 연계 | 독립 작업셋에서 통계적 불확실성과 반복 평가를 포함한 실익 확인 |
| D. Model Learning | 품질·권리·PII·중복·평가 누출 심사를 통과한 경험만 SFT/QLoRA 후보 데이터로 가공; 옵트인 후 GPU 학습 | 자가학습 후반부 | 기준 모델 대비 보지 못한 저장소/문제에서 성능 개선, 악화 시 폐기 |

**최소 경험 이벤트 계약(예시; 실제 DB/이벤트 설계는 기존 구조를 확인한 뒤 확정):**

```json
{
  "event_version": 1,
  "task_id": "task-001",
  "agent_id": "coding",
  "repo_revision": "<commit-sha>",
  "task_category": "api_bugfix",
  "strategy_id": "reproduce_minimal_patch_test",
  "attempt_count": 2,
  "test_result": "passed",
  "independent_evaluation": "not_run",
  "elapsed_ms": 84000,
  "artifact_refs": ["<approved-diff-or-test-report-id>"],
  "outcome": "candidate_only"
}
```

**기록·검색 안전 규칙:**
- `.env`, 계정 토큰, 사내 정보, 개인 대화 원문, 전체 소스 덤프·도구 로그 원문을 무조건 저장하지 않는다. 먼저 최소 필드만 기록하고, 원본 로그는 별도 격리 저장·보존 기간·접근 정책 적용.
- '테스트 통과'는 '전략의 일반화된 성공'과 다르다. 검증셋을 반복 열람하며 전략 점수를 올리지 않으며, **숨김 최종 평가셋은 학습 및 검색에 사용하지 않는다.**
- 특정 사례를 찾았다는 이유로 외부 저장소 코드나 명령을 그대로 실행하지 않는다. 출처·사용조건·리비전을 함께 관리.
- 전략 적용은 기본적으로 제안 방식이며, 사용자 승인/권한 정책을 우회하지 않는다. 성능 저하 시 Retriever/Optimizer를 즉시 비활성화할 수 있어야 한다.
- Preview Lab에서 사용자가 지적한 '의도 차이'와 수정/검증 결과도 **사용자 동의 및 최소 데이터** 기준으로 기록해 요구사항 해석 전략을 개선한다.
- 실패 기록을 버리지 않는다. 단, 우연히 테스트를 통과하거나 작은 표본에서 높은 점수를 기록한 사례는 우수 전략으로 자동 승격하지 않는다.

### 4.3 Multimodal Requirement Understanding
- 텍스트·PDF: 텍스트 파싱 우선; 스캔 문서는 선택적 OCR/레이아웃 분석.
- 이미지/설계도: 소형 VLM을 필요 시 로드하여 화면 요소·관계·불확실성 추출.
- 영상: FFmpeg/OpenCV 장면·동작 후보 구간 검출→핵심 프레임만 VLM 처리. 관찰되지 않은 DB/API 동작은 '추정' 표시.
- 결과: `requirements` 개별 ID, 출처 프레임/페이지, 신뢰도, 미결정 사항, 테스트 가능 acceptance criteria.

### 4.4 Preview Lab 및 Intent Gap Detector
- L1 UI Preview: 정적 화면, 모의 데이터.
- **L2 기본** Interactive Preview: 버튼·폼·화면 전환이 되는 경량 앱, 외부 시스템 영향 없음.
- L3 Functional Prototype: 격리된 일부 핵심 실제 동작.
- 2개 대안 비교는 복잡 작업에서 선택 실행; 사용자 피드백 시 해당 영역만 재생성.
- Intent Gap Detector는 `요구사항 ID → 화면/동작/테스트/코드` 매핑에서 누락·다름·검증 불가를 구분.
- 승인된 프로토타입의 명세/테스트는 본 개발 기준으로 유지. 모의 보안/인증 코드를 운영 코드로 자동 승격하지 않음.

### 4.5 Research & Knowledge Engine
- 기본 탐색: GitHub, Hugging Face, arXiv, OpenReview, 공식 기술 문서. 확대 후보: Kaggle, Stack Overflow, OpenAlex 등.
- 키워드 없이 모드 실행 시 **현재 기능 목록·실패 기록·성능 병목으로 검색 쿼리 자동 생성**.
- 논문·코드는 발견 사실과 성능 검증을 구분; 논문 자체의 주장만으로 자동 채택 금지.
- 수집 정책: API·robots/약관/라이선스, ETag/커밋/리비전 기반 증분 갱신, rate limit, 네트워크 허용 목록.
- 원본 형식 유지(PDF/CSV/Parquet/JSON/코드 등) → 공통 스키마 → 정제 → 목적별 JSONL.
- 전처리: 출처·버전·라이선스·해시, 개인정보/비밀 검사, 정규화, 정확 중복 SHA-256, 유사 중복 MinHash/SimHash, 의미 중복 후보, 구조 기반 chunking, 품질 점수.
- 서로 다른 코드 버전이나 상충되는 연구는 삭제하지 말고 이력·관계 유지.
- RAG용·SFT/DPO 학습용·독립 평가용 데이터는 분리. 평가 누출·중복은 소스/리포지터리/문제 계열 단위로 확인.
- RAG: 텍스트 검색 + 임베딩 캐시 + 선택적 Reranker, 출처·버전 표시; 한국어 검색 평가 후 모델 선택.

### 4.6 선택적 실제 모델 자가학습
- 지식 최신화(RAG), 에이전트 스킬 개선, LLM 가중치 학습을 구분.
- 충분히 검증된 학습 후보에서 SFT/LoRA/QLoRA 또는 선호 비교 기반 DPO/RLAIF를 선택.
- 사용자 대화 원문이나 테스트 정답을 무분별하게 학습시키지 않는다.
- Coding Strategy Learner의 검증된 경험은 권리·개인정보·품질·평가 누출 검사를 통과한 경우에만 별도 학습 후보로 등록한다. 학습 데이터 생성과 실제 모델 학습을 분리한다.
- RTX 4060 Ti 8GB에서 작은 모델/작은 데이터로 우선 검증; 실험마다 기반 모델·어댑터·해시·비용·실패 기록.
- 보류/승인된 학습 작업만 비대화 시간에 실행; 기존 운영 모델을 자동 교체하지 않음.

### 4.7 Discovery Mode + AI Evolution Lab
- 사용자가 **자가개선 모드 실행**을 누르면 외부 검색 키워드 없이 내부 기능 목록과 병목 기준으로 기술 탐색.
- 후보별 `가설 → 근거 → 변경안 → 격리 실험 → 반복 평가 → 결정` 생성.
- 다른 프로젝트 전체 교체, 특정 알고리즘 부분 반영, 보류, 폐기 네 가지 분류.
- 후보는 기존 방식·외부 구현·Role-AI가 제안한 대안 등을 비교하며, 실패 기록도 재실험 조건과 함께 저장.
- 테스트 반복(예: 3~5회)은 시작값일 뿐; 독립 테스트 사례·p50/p95 지연·정확도·작업 성공률·VRAM/CPU/RAM·보안·회귀를 함께 평가.
- 개발용 공개셋 / 검증셋 / 숨김 최종 평가셋 엄격히 분리. 최종셋은 승격 시 제한적으로 사용하고 개선 에이전트에 정답 비공개.
- 실제 시스템에 영향을 주는 모든 후보는 최종적으로 사용자 승인 요구.

### 4.8 Adaptive Runtime / Performance Governor
- 상시 실행: 최소 API·메타데이터·작업 큐·가벼운 UI. **LLM/VLM/OCR/학습은 요청 시 동적 활성화**.
- 우선순위: P0 실시간 대화, P1 사용자 코딩, P2 멀티모달, P3 백그라운드 연구, P4 학습.
- GPU 단일 작업 기본, CPU 전처리 병렬 처리; 메모리·KV cache·모델 keep-alive·전환 비용 측정.
- 대화 응답: 스트리밍(SSE 등) 후보 + 모델 상주 정책, 단순 요청은 에이전트 추가 호출 없음.
- 캐시: 검색 임베딩, 문서 파싱, 코드 인덱스, 웹 리비전, 검증 결과; 각 캐시에 TTL/프로젝트·사용자 범위/버전 무효화.
- 학습 작업은 안전한 체크포인트에서 중지/재개; 즉시 선점 가능하다고 가정하지 않음.
- 프로필: Fast / Balanced / Quality. 정확성과 필수 보안 검사 희생 금지.

### 4.9 AI Office / Agent Activity Dashboard (마지막 UI 단계)
- 6개 논리 역할을 가상 사무실/캐릭터로 표현. 일함·대기·승인 대기·일시 중지·오류·연결 불명 구별.
- 작업별 실제 단계, 큐 순서, 로그, 사용 모델, 소요시간, CPU/RAM/VRAM, 이벤트 추적을 제공.
- 미확정 작업 전체 진행률을 가짜 퍼센트로 표현하지 않음; 작업 단계 또는 추정치 표시.
- 백엔드는 초기부터 `task_id`, `agent_id`, `status`, `phase`, `started_at`, `updated_at`, `heartbeat`, `progress`, `requires_approval`, `resource_usage` 이벤트 계약을 남긴다.
- UI는 PC/모바일 별도 반응형 설계, 모션 최소화·접근성·연결 끊김 상태 지원.
- 상태 반영에는 SSE/WebSocket 중 단순한 방식을 평가하여 선택.

## 5. 제안 데이터·설정 계약

```text
config/                       # Role-AI가 관리하는 정책; 실제 런타임 설정과 어댑터 연동
  agents/                      # 현재 dev: JSON 정의 기반, 내장 default; 실제 파일 존재 검증
  inference/                   # 모델, 컨텍스트, 시간·토큰 상한
  research/                    # 사이트별 허용 범위/일정/라이선스
  evaluation/                  # 테스트 정책과 승격 게이트
  runtime/                     # GPU 예약·우선순위·캐시 정책
prompts/                       # 버전/해시가 있는 역할별 지침
artifacts/                     # 운영 볼륨 외 격리된 시제품·스크린샷·테스트 출력
learning/
  raw/                         # 원본 보존(접근제어·보존정책 적용)
  normalized/                  # 공통 구조, 가능하면 Parquet
  processed/                   # 정규화·중복/품질 검사·chunk
  datasets/{rag,sft,dpo,evaluation}/ # 용도별 JSONL/벡터 인덱스
  manifests/                   # 원본 주소/버전/해시/라이선스/계보
```

이 디렉터리는 **논리적 예시**다. 실제 물리 경로는 기존 SQLite DB·Docker named volume·백업 정책을 확인한 뒤 결정한다. 대규모 원본/임베딩은 Git에 커밋하지 않는다. 비밀키는 정책 파일에 두지 않는다.

## 6. 품질 게이트와 완료 기준

- **실제 동작:** Mock 기반 테스트와 실모델/실브라우저/실GPU 측정을 별도 표기.
- **품질:** 동일 조건 기준선 대비 작업 성공률, 한국어 의도 충실도, 수정/테스트 통과율, RAG Recall@K, 사용자 의도 차이 탐지 정확도.
- **속도:** TTFT, p50/p95 총 응답시간, 모델 재로드 지연, 파싱/검색/도구 실행 세부 시간.
- **비용:** VRAM 피크, RAM, CPU, 저장소 증가, 네트워크 호출, 에너지 지표는 측정 가능 범위에서.
- **안전:** 미인가 파일 접근/명령 실행/외부 데이터 전송/운영 변경 0건, 격리 우회 테스트, 보안 회귀 통과.
- **일반화:** 코딩은 다른 저장소/이슈 유형, 모델은 미학습 문제, UI는 다른 화면 크기·브라우저 테스트.
- **승격:** 독립 평가 우세, 중대한 회귀 없음, 라이선스 적합, 사용자 승인, 되돌리기 검증 필요.
- **완료 판정:** 기능 버튼 존재나 테스트 스텁만으로 완료 처리 금지. 실제 사용자 시나리오 end-to-end 결과 첨부.

## 7. 구현 순서 — 새 기능 욕심보다 수직 완성

| 단계 | 구현 묶음 | 통과해야 다음 단계 진입 |
|---|---|---|
| 0 | 코드/운영 기준선 및 실측, 기능 현황, 테스트·권한 경계 | 현재 모델 속도·VRAM·작업성공 기준선 문서화 |
| 1 | **Coding Agent MVP + Strategy Recorder**: 파일 변경·격리 실행·테스트·PR 후보, 작업 경험 최소 메타데이터 기록 | 소규모 저장소 과제에서 반복 수행 성공 + 민감정보 없는 기록/복구 검증 |
| 2 | **Preview Lab L1/L2 + Intent Gap + Strategy Retriever(조건부)** | 시제품 피드백 반영; 충분한 경험이 모인 경우에만 관련 전략 검색의 실제 개선 확인 |
| 3 | PDF/이미지/영상 요구사항 처리 + 시각적 QA | 출처 추적 가능한 요구사항과 화면 검증 |
| 4 | HF/GitHub/arXiv 연구 수집·전처리·RAG·기억 보강 | 증분 수집·중복 제거·근거 회수 평가 |
| 5 | **Strategy Optimizer + 선택적 실제 학습 + AI Evolution Lab** | 독립 평가에 기반한 전략 비교; 검증 경험의 선택적 학습·롤백 |
| 6 | 개인화·Adaptive Runtime 최적화 | 동일 품질 이상에서 실측 지연·자원 개선 |
| 7 | **AI Office 최종 UI** 및 전체 회귀·접근성 | 실제 상태 연동, 허위 진행률 없음, 운영 승인 절차 검증 |

Strategy Recorder 기록 계약과 성능 관리/상태 이벤트/권한 경계는 0~1단계부터 횡단 적용한다. **Retriever는 경험이 충분하고 기준선 평가를 통과할 때에만**, Optimizer는 Evaluation Agent 활성화 이후에 연결한다. 4·5단계는 기존 구현을 재사용하므로 세부 PR 분할은 코드 실사 후 조정할 수 있다.

## 8. Codex 첫 작업 결과물 형식

다음 파일을 **먼저 제안/생성(승인 범위 확인 후)** 한다:

1. `docs/plans/ROLE_AI_1_0_GAP_ANALYSIS.md` — 현재 dev 기준 기능 매트릭스, 파일 경로와 근거.
2. `docs/plans/ROLE_AI_1_0_PR_PLAN.md` — PR 순서, 의존성, 변경 예정 파일, 테스트와 롤백.
3. `docs/plans/ROLE_AI_1_0_BASELINE.md` — 측정 가능/불가, 실측 결과, Mock 구분.
4. `docs/plans/ROLE_AI_1_0_SECURITY_GATES.md` — Gateway·실행·파일·외부 전송 승인 경계.
5. `docs/plans/CODING_STRATEGY_LEARNER_ROLLOUT.md` — Recorder→Retriever→Optimizer→Model Learning의 PR, 최소 기록 스키마, 데이터 보호, 테스트 및 단계별 활성화 조건.

**이번 최초 지시는 분석/계획만**이다. 사용자 승인 전 코드 수정, 모델 다운로드, 실제 학습 실행, 기존 Docker/DB/운영 환경 변경 금지.

## 9. Codex에 붙여 넣을 시작 프롬프트

> 이 문서 `ROLE_AI_1_0_CODEX_IMPLEMENTATION_PLAN.md`를 제품 기획 기준으로 사용하되, 현재 `dev`의 실제 코드를 단일 진실 공급원으로 삼아라. 먼저 HEAD/작업 트리/운영 구성과 주요 소스를 읽어 '구현됨·부분 구현·미구현·실환경 미검증' 매트릭스를 작성하라. 기존 OpenClaw 두 Gateway의 보안 분리를 유지하라. 변경 계획은 0~7단계로 쪼개고, 1순위는 격리된 Coding Agent MVP와 반복 테스트·PR 후보 생성, 그리고 민감정보 없는 Strategy Recorder 기록 기반이다. Retriever/Optimizer/Model Learning은 이 단계에서 활성화하지 마라. 기존 데이터/인증/비밀/운영 배포/유료 API를 변경하지 말고, **첫 응답에서는 코드 변경 없이** GAP 분석, 우선순위, 예상 위험, 테스트 계획을 보고하라. 추후 명시적으로 승인받은 작은 기능 PR부터 구현하라.
