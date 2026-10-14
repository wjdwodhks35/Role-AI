# Role-AI 하이브리드 설계

## 현재 구현과 최종 목표

현재 경로: 브라우저 → FastAPI 인증/대화 → ProviderAssistant → Ollama 또는 명시적 Mock.
최종 경로: 브라우저/Windows 클라이언트 → HTTPS → Role-AI → 제한된 Assistant Gateway Adapter
→ OpenClaw → 허용된 Role Skills / Model Provider / 도구.

Phase 1의 ProviderAssistant는 OpenClaw 도입 전까지 사용할 수 있는 교체 가능한 기본 비서입니다.
AssistantEngine은 사용자·대화 식별자를 포함한 AssistantContext를 받습니다.
OpenClawGatewayAdapter는 현재 호출을 명시적으로 거부합니다. 단일 인터페이스로 교체하되
공개 사용자와 소유자 전용 PC 제어 경로를 분리하는 것이 후속 단계의 전제입니다.

Role-AI는 인증, 데이터 소유권, 기억/RAG, 권한, 작업 상태를 관리합니다.
OpenClaw는 계획과 Skill/Tool 선택을 담당합니다.
ProviderManager는 동일한 complete/probe 계약으로 모델 교체를 지원합니다.
현재 OpenAI와 vLLM은 예약된 구현이며 모델 라우팅/비용 한도는 아직 없습니다.
Skill은 한 공유 모델의 프롬프트를 바꾸므로 별도 대형 모델을 역할마다 상주시킬 필요가 없습니다.
선택은 Phase 1에서 사용자가 직접 수행하며 자동 Skill 선택은 후속 기능입니다.

## 공식 계약 확인 (2026-10-14)

- [OpenClaw Chat Completions](https://docs.openclaw.ai/gateway/openai-http-api):
  HTTP 엔드포인트는 기본 비활성화이며 Gateway 설정에서 활성화합니다.
  shared-secret Bearer 인증은 운영자 수준의 권한이므로 사용자별 격리로 취급하지 않습니다.
  /v1/chat/completions의 model은 에이전트 대상이며 일반 LLM 모델 ID와 구분해야 합니다.
  실제 연결 시 비공개 네트워크, 소유자 검증, 서버에서 생성한 대화 키를 사용합니다.
- [Ollama Chat API](https://docs.ollama.com/api/chat):
  Phase 1은 POST /api/chat의 비스트리밍 응답을 사용합니다.
  Provider 오류/타임아웃은 정상 응답으로 대체하지 않습니다.
- [OpenAI API reference](https://platform.openai.com/docs/api-reference/responses):
  OpenAI 실제 연결은 다음 기능 PR에서 공식 API 계약을 다시 확인하고 구현합니다.
  ChatGPT 웹 구독을 API 무료 사용량으로 간주하지 않으며 세션 쿠키 우회는 사용하지 않습니다.

## 데이터 경계

User → AuthSession / Conversation → Message.
Repository의 대화 조회 및 저장은 항상 인증된 user_id로 제한됩니다.
Password는 해시, 세션 식별자는 해시, CSRF 토큰은 세션별 서버 데이터로 저장합니다.
데이터는 로컬 SQLite에만 존재하며 GitHub에 사용자 DB를 올리지 않습니다.
SQLAlchemy와 Repository를 분리했지만 PostgreSQL 지원 및 마이그레이션은 미구현입니다.

모델 출력은 장기 기억으로 자동 저장하지 않습니다. 대화 기록과 검증된 장기 기억을 구분합니다.
Memory/RAG 구현 시 원문 출처, 수정/삭제, 보존 기한, 사용자 승인·검증 상태를 추가합니다.
UI는 모델/사용자 텍스트를 textContent로 표시하여 HTML 실행을 막습니다.
프롬프트는 신뢰되지 않는 데이터라는 규칙을 포함하지만 프롬프트만으로 권한을 보장하지 않습니다.
실제 도구는 현재 없고 PermissionGate는 모두 거부합니다.

## 운영 경계

현재 Compose는 API 한 프로세스와 SQLite volume만 제공합니다.
모델은 호스트 Ollama를 사용합니다. 공유 모델은 연속 호출 시 keep_alive로 재사용합니다.
GPU 자원 스케줄러, 모델 언로드, Whisper 충돌 방지, 양자화/CPU offload 관리 기능은 미구현입니다.
공개 연결은 Cloudflare Tunnel 등 HTTPS 설정 후 별도 단계로 진행합니다.
공개 사용자 요청을 소유자 전용 OpenClaw 인스턴스로 바로 전달하지 않습니다.
향후 Worker는 비동기 작업을 별도 실행하며 승인된 입력만 받아야 합니다.

## 알려진 제한

브라우저 E2E, 실제 GPU 추론과 공식 서비스 인증은 이 저장소의 Mock 테스트로 검증되지 않습니다.
Rate Limit은 단일 프로세스이고 trusted proxy headers를 기본적으로 수용하지 않습니다.
메일 인증, 계정 복구, 사용자 관리 관리자 UI, 장기 기억, 보존/삭제·감사 정책은 후속 단계입니다.
UI에는 로그인/가입, 대화, 기록, Provider/Skill 선택, 연결 테스트와 상태만 있습니다.
회의·파일 업로드·마이페이지·API 키 설정·권한 승인 화면은 구현 완료로 표시하지 않습니다.
