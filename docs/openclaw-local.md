# 로컬 소유자용 OpenClaw 연결

현재는 Windows/WSL2 개인용 개발만 진행합니다. 공개 배포는 계획하지 않으며,
향후 Linux 서버 구축 시 별도 검증합니다.

## 범위

공식 HTTP Chat Completions와 models 목록 조회를 구현했습니다.
기본값은 기존 ProviderAssistant이며 UI에서 OpenClaw를 명시적으로 선택합니다.
소유자만 OpenClaw를 사용할 수 있고 비소유자/공개 모드 요청은 네트워크 호출 전 차단합니다.
실패 후 자동 재시도/자동 fallback은 하지 않습니다. 기본 비서로 직접 전환하세요.
실제 Gateway와 GPU 환경은 사용자 PC에서 별도 검증해야 합니다.

## Gateway 설정

이미 설치된 OpenClaw의 실제 시작 설정에 다음 정책을 반영하세요.
설정을 덮어쓰지 말고 현재 버전의 공식 문서와 함께 검토하세요.
이 어댑터는 strict JSON만 읽습니다. JSON5 설정은 JSON으로 정리해야 합니다.

    {
      "gateway": {
        "auth": { "mode": "token" },
        "http": { "endpoints": { "chatCompletions": { "enabled": true } } }
      },
      "agents": {
        "defaults": {
          "model": { "primary": "ollama/qwen2.5:7b", "fallbacks": [] }
        }
      },
      "tools": { "deny": ["*"] }
    }

OPENCLAW_GATEWAY_TOKEN은 Gateway 서버 환경에 별도로 설정하세요.
Role-AI의 ROLE_AI_OPENCLAW_TOKEN에는 같은 비밀을 설정합니다. 저장소에 넣지 마세요.
Gateway는 루프백에 바인딩하고 Ollama Provider는 로컬 서버를 사용하도록 설정합니다.
이 예시는 완전한 설치 파일이 아니라 필수 정책 부분입니다.
기존 설치의 Ollama 모델 등록 및 Gateway 실행은 공식 설치 문서를 따릅니다.

Role-AI 환경변수:

    ROLE_AI_OPENCLAW_ENABLED=true
    ROLE_AI_OPENCLAW_URL=http://127.0.0.1:18789
    ROLE_AI_OPENCLAW_TOKEN=<Gateway 서버와 동일한 비밀>
    ROLE_AI_OPENCLAW_CONFIG_PATH=/실제/Gateway/시작설정.json

Role-AI와 Gateway가 같은 WSL2 환경에서 실행되는 구성을 우선합니다.
이번 버전은 루프백 URL만 허용하므로 host.docker.internal이나 원격 IP는 지원하지 않습니다.
Compose 안의 127.0.0.1은 컨테이너 자신을 가리킵니다. OpenClaw 테스트는 README의
WSL2 직접 실행 방식으로 진행하고, Gateway 컨테이너화는 후속 작업으로 미룹니다.

## 정책 검증의 의미

각 요청 전 실제 시작 설정 파일에서 전체 도구 deny, 로컬 Provider 접두사,
빈 모델 fallback, 기본 에이전트만 사용, token 인증, HTTP endpoint 활성화를 검사합니다.
이 검사는 파일의 정적 검사이며 실행 중 Gateway의 설정을 증명하는 원격 attestation은 아닙니다.
다른 시작 파일이나 런타임 설정을 적용하지 마세요.
Provider 이름만으로 실행 위치/요금이 보장되는 것은 아니므로 Gateway Provider 주소도
본인의 로컬 Ollama/vLLM인지 확인하세요. 유료 Provider는 이 단계에 연결하지 않습니다.
Gateway 내부 도구를 Role-AI Permission Gate가 가로챌 수 있다고 가정하지 않습니다.
따라서 전체 도구 차단 설정이 전제이며, 도구/PC 제어는 이번 연결에서 제공하지 않습니다.
돌려받은 tool_calls도 실행하지 않고 실패로 처리합니다.

## 상태와 대화

UI의 OpenClaw 연결 테스트는 GET /v1/models의 기본 에이전트 존재를 확인합니다.
이 테스트는 모델 추론 성공을 보증하지 않습니다. 실제 채팅 응답으로 추론을 검증하세요.
API 상태는 설정만으로 connected를 표시하지 않습니다.
DB에 기록하는 model=openclaw/default는 Gateway의 에이전트 대상이며 실제 LLM 이름이 아닙니다.

서버가 인증 사용자 ID + 대화 ID + Skill로 해시한 user 값을 Gateway로 전송합니다.
같은 조합은 같은 Gateway 세션을 사용하고 다른 대화/Skill은 분리됩니다.
사용자 이메일과 클라이언트가 지정한 session 키를 전달하지 않습니다.
Gateway에는 최신 사용자 메시지만 보내 중복 히스토리 전송을 피합니다.
기본 비서의 과거 기록이 Gateway에 자동 이관되지는 않습니다.
기본 비서로 돌아가면 Role-AI에 저장된 최근 대화 기록을 사용할 수 있습니다.
전송 실패/타임아웃은 Gateway가 이미 처리했을 가능성이 있으므로 자동 반복하지 않습니다.
동시 대화 요청은 단일 프로세스에서 직렬화/거부합니다. 다중 프로세스 실행은 지원하지 않습니다.

## 공식 문서 확인 (2026-10-14)

[HTTP API](https://docs.openclaw.ai/gateway/openai-http-api):
token 인증은 운영자 권한이며 /v1/chat/completions는 기본 비활성화입니다.
model은 에이전트 대상, user는 안정적인 대화 세션 연결에 사용됩니다.

[Tool policy](https://docs.openclaw.ai/gateway/config-tools/tool-policy):
deny 정책이 우선하며 * wildcard를 지원합니다.
이 구현은 tools.deny=["*"]를 요구합니다.

## 코드 테스트 역할

OpenClaw를 명시적으로 선택한 뒤 역할 목록에서 **코드 테스트**(`code_tester`)를 선택합니다.
동일 역할은 기본 ProviderAssistant에서도 사용할 수 있으며 별도 모델을 적재하지 않습니다.
코드·요구사항·언어/테스트 프레임워크·오류 로그를 제공하면 테스트 설계, 테스트 코드 작성,
정상/경계/예외/회귀 사례와 실패 원인 분석을 돕습니다. 예상 결과와 실제 로그를 구분합니다.

현재는 테스트 설계·분석 역할이며 실제 테스트 실행 봇은 아닙니다.
파일 접근·shell 실행 기능은 없고 `tools.deny=["*"]` 정책을 유지합니다.
제안받은 테스트는 사용자가 격리된 로컬 환경에서 실행하고 결과를 다시 제공해야 합니다.
OpenClaw의 실제 모델 응답 품질은 별도로 연결 후 검증해야 합니다.
