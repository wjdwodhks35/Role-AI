# OpenAI Provider와 비용 정책

현재 개발 대상은 Windows/WSL2의 개인용 환경입니다. 공개 서비스 배포는 수행하지 않습니다.
이번 기능은 공식 Responses API를 구현하며 실제 키를 사용한 호출은 CI에서 하지 않습니다.

## 서버 설정

.env에 아래 값을 직접 설정한 후 Role-AI를 재시작합니다.

    ROLE_AI_OPENAI_ENABLED=true
    ROLE_AI_OPENAI_API_KEY=<본인의 API 키>
    ROLE_AI_OPENAI_MODEL=<Responses API 지원 모델 ID>
    ROLE_AI_OPENAI_INPUT_USD_PER_MILLION=<선택한 모델의 입력 USD 단가>
    ROLE_AI_OPENAI_OUTPUT_USD_PER_MILLION=<선택한 모델의 출력 USD 단가>
    ROLE_AI_OPENAI_DAILY_BUDGET_USD=<서버 전체 UTC 일일 예산>
    ROLE_AI_OPENAI_REQUEST_BUDGET_USD=<호출당 예산>
    ROLE_AI_OPENAI_MAX_OUTPUT_TOKENS=512

단가·예산 기본값은 0이며, 0이면 호출을 막습니다. 가격을 자동 추정하지 않습니다.
[OpenAI 공식 가격](https://openai.com/api/pricing/)에서 선택 모델의 최신 가격을 확인해 설정하세요.
가격이 바뀌면 설정도 변경해야 합니다. 서버 키는 환경변수 SecretStr로만 보관하고
API 응답/브라우저/localStorage에 전달하지 않습니다. 사용자별 API 키 등록 UI는 아직 없습니다.
초기 소유자 계정으로 로그인하고 Provider를 OpenAI로 선택합니다.
기본 Provider는 Ollama로 유지하며 자동으로 유료 모델을 선택하지 않습니다.
모델 변경은 서버의 허용된 단일 모델 설정과 재시작을 통해 수행합니다.

## 비용 확인과 동의

첫 보내기는 예상 예약 비용과 남은 예산을 조회하고 **추론을 호출하지 않습니다**.
금액과 외부 데이터 전송을 확인한 후 동의 체크를 선택하고 다시 보내면 호출합니다.
메시지/Skill/Provider/비서 엔진 변경 시 동의는 초기화됩니다.
서버도 요청별 allow_paid, 소유자 권한, 활성화 상태와 예산을 다시 검사합니다.
OpenClaw 경로에는 이 Provider 예산이 적용되지 않으며 기존 전체 도구 차단/로컬 모델 정책을 유지합니다.
이 서버의 API 키를 비소유자 계정으로 사용할 수 없습니다.

## 사용량 장부

SQLite의 새 api_usage 테이블에 입력/출력 토큰, 모델 설정, 단가 스냅샷,
예약 비용, 계산 비용과 상태를 저장합니다. 기존 사용자/대화 테이블은 변경하지 않습니다.
USD를 정수 micro-USD로 저장해 부동소수점 누적을 피합니다.
사용량 화면은 소유자 자신의 최근 기록만 표시하며 서버 예산은 모든 예약을 합산합니다.

호출 전 BEGIN IMMEDIATE 트랜잭션으로 예산을 예약하여 동시 호출의 한도 우회를 막습니다.
텍스트의 UTF-8 크기와 여유분, max_output_tokens로 보수적인 예상치를 계산합니다.
정확한 토크나이저나 실제 청구 상한 보장은 아닙니다.
수신한 usage의 input_tokens/output_tokens를 설정 단가로 계산해 예약을 정산합니다.
output_tokens에 포함된 추론 토큰을 별도로 중복 가산하지 않습니다.
캐시 할인은 적용하지 않으므로 계산 비용은 실제 청구서와 다를 수 있습니다.
응답에 usage가 없거나 HTTP 실패·타임아웃·취소가 발생하면 예약을 유지하며 uncertain으로 기록합니다.
이전 UTC 날짜의 미확정 예약도 다음 날 예산 계산에 포함됩니다.
프로세스 종료로 reserved 상태가 남아도 임의로 반환하지 않습니다.
불명확한 기록의 실제 청구 확인 및 관리자가 수행하는 정산 도구는 후속 기능입니다.
API 키를 다른 프로그램에서 사용한 비용은 이 로컬 장부에 포함되지 않습니다.

## API 동작과 제한

POST https://api.openai.com/v1/responses에 비스트리밍 텍스트 요청을 보내며
store=false, 빈 tools, tool_choice=none 및 max_output_tokens를 적용합니다.
OpenAI의 실제 response model을 답변 메타데이터로 표시합니다.
자동 재시도·유료 장애 대체를 하지 않으며 모델 API GET 테스트는 추론 검증으로 표시하지 않습니다.
실패한 응답에도 usage가 있으면 비용을 정산합니다.
도구 호출·불완전 응답·비텍스트 응답을 성공으로 가장하지 않습니다.
도구 실행/검색/이미지/음성/스트리밍/자동 라우팅과 사용자별 키 등록은 범위 밖입니다.
store=false는 응답 저장 옵션이며 모든 데이터 보존을 없앤다는 보장이 아닙니다.

## 공식 문서 확인 (2026-10-14)

- [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [추론 토큰과 출력 한도](https://developers.openai.com/api/docs/guides/reasoning)
- [API 데이터 정책](https://developers.openai.com/api/docs/guides/your-data)
- [ChatGPT와 API의 별도 결제](https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform)

ChatGPT Plus/Pro 구독을 API 사용량으로 간주하지 않습니다.
비공식 세션 쿠키나 인증 우회를 사용하지 않습니다.
