# Codex 사용량 근거 수집 규약 — A 생성 원문 확보, 전체 절감률 미측정

2026-10-31 KST. OpenAI Docs 스킬에 따라 공식 문서를 검색/열람했다. API 호출·계정 사용량 조회·새 Codex 실행·확장 설정 변경·개인 대화 로그 열람을 수행하지 않았다. 아래는 제공 형식의 공식 근거이며 현재 실험의 수집 완료 수신이 아니다.

## 확인된 공식 경로

- 비대화형 CLI의 `codex exec --json`은 JSONL 이벤트를 출력하고 `turn.completed` 이벤트의 `usage`에 input_tokens/cached_input_tokens/output_tokens 등의 계측 필드를 담는 예시가 공식 문서에 있다. 이는 명령 실행을 권고/승인한 것이 아니며 현재 VS Code 세션에서 그 파일이 확보됐다는 의미도 아니다. [OpenAI 공식 non-interactive 문서](https://learn.chatgpt.com/docs/non-interactive-mode#make-output-machine-readable)
- App Server는 활성 thread의 `thread/tokenUsage/updated` notification을 문서화한다. 해당 프로토콜 제공 사실이 현재 VS Code UI의 작업별 export 기능이나 기존 연결에서의 원문 확보를 보장하지 않는다. 기존 앱 연결을 가로채거나 신규 계측 클라이언트를 자동 삽입하지 않는다. [OpenAI 공식 App Server 문서](https://learn.chatgpt.com/docs/app-server)
- 같은 App Server의 계정 lifetime/daily summary는 과제별 관측 구간이 아니다. 이를 특정 과제 또는 A/B 방식의 사용량으로 비례 배분하지 않는다. [공식 account usage 설명](https://learn.chatgpt.com/docs/app-server#7-token-usage-chatgpt)

## 실제 실험에 사용할 때의 조건

1. 원문이 실제 평가 세션/작업/turn과 연결돼야 한다. A/B 모두 작업 정의·수정·오류 개입·최종 검토를 포함하는 동일 비용 구간을 사전에 고정한다. 다른 대화·일일/계정 총합을 섞지 않는다.
2. 원문은 private 로컬 보관하고, 대화/명령/파일 본문을 GitHub나 외부 모델로 보내지 않는다. 공유 보고에는 필요한 숫자·필드 정의·출처 hash·평가 ID만 기록한다.
3. input/output/cached/reasoning 등 표시된 범주를 보존한다. 중복 포함 여부와 해당 client 버전의 정의를 확인하기 전 합계를 임의로 만들지 않는다. 잘린 로그·실패/중단 turn·없어진 usage는0으로 채우지 않는다.
4. 제공된 원문과 candidate/task/manifest/context 검토를 실제로 대조한 뒤 receipt를 작성한다. hash가 있다는 이유만으로 제공자/공정성을 인증했다고 판정하지 않는다.
5. 현재 Role-AI `MetricRecord`의 codex_tokens와 codex_usage_receipt_sha256는 사용량 증거를 표현하는 필드이며 자동 수집기가 아니다. 현재 평가 CLI가 공식 원문을 검증해 이 필드로 연결하는 수집/검증 경로는 별도 확정이 필요하다. 숫자나 임의 JSON을 넣어 완료 조건을 통과시키지 않는다.

## 현재 상태

실제 A/B 제출 및 작업별 사용량 원문이 없으므로 Codex 토큰·절감률은 null이다. 공식 계측 경로가 존재한다는 문서 근거와 이 환경에서의 확보 여부를 구분한다. 기존 VS Code 작업 방식을 바꾸지 않는 원문 확보가 가능한지 확인한 뒤 진행하며, 불가능하면 원래 요청이 허용한 미측정/null 보고를 유지한다. 별도 완료 기준 변경 승인은 필요하지 않으며 토큰 절감률은 검증하지 못한 것으로 표시한다.

## 후속 확인

로컬 `codex login status`는 exit0/ChatGPT 인증 방식으로 분류됐다. API key 방식이 아니며 상태 원문·계정·토큰은 공개하지 않았다. 로그인/로그아웃·계정 변경·새 Codex 추론은 수행하지 않았다. 이 확인은 추가 에이전트 실행 승인이나 실제 과제별 usage 수신이 아니다. [공식 CLI 인증 문서](https://learn.chatgpt.com/docs/auth?surface=cli)

## 승인 후 실제 A 생성 usage 수신

사용자가 추가 독립 Codex 실행을 명시 승인했다. CLI0.164.0 fresh10프로세스·기존 ChatGPT auth/forced login·도구 비활성·readonly·ephemeral·공개 payload만으로 답안을 생성했다. 실제 turn.completed.usage10개를 원문 대조: input130510/cached_input24064/cache_write0/output2261/reasoning_output390. 범주 중복 가능성을 보존하며 총합을 임의로 만들지 않는다. 원문은 Git 밖 `p2e-a-only-tpjpbd2o/records/*/events.jsonl`이다.

이 원문은 A 생성 구간이다. B Codex 작업 정의·수정·최종 검토 구간과 일치하는 원문이 없어 전체 codex_tokens·절감률은 null이다. A/B CLI/Ollama 서로 다른 토큰을 나누지 않는다. 이전 미확보 문단은 당시 상태이며 이 절과 PERFORMANCE_REPORT를 현재 수신으로 사용한다. 새 로그인/API key/유료 API·전역 설정 변경은 없었다.
