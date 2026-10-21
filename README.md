# Role-AI

사용자 데이터와 권한을 관리하는 FastAPI 플랫폼 위에 OpenClaw Assistant Engine,
교체 가능한 LLM Provider, 전문 Skill을 연결하는 개인용 AI 비서 프로젝트입니다.
이 저장소의 코드는 설계 기반 **새로운 Phase 1 구현**이며, 확보되지 않은 기존 소스의 복원본이 아닙니다.

## Phase 1 범위

| 기능 | 구현 상태 |
| --- | --- |
| FastAPI / 환경변수 / 한국어 반응형 UI | 구현 |
| SQLite / SQLAlchemy / Repository 분리 | 구현 |
| 회원가입(기본 비활성화), 로그인, 로그아웃, 사용자별 대화 | 구현 |
| 비밀번호 해시 / 만료 세션 / CSRF / 소유권 검사 / 요청 크기 제한 | 구현 |
| Ollama 대화, 모델 설치 확인, 타임아웃, 안전한 오류 표시 | 구현, 실제 사용자 PC 연결은 별도 검증 필요 |
| Mock Provider | 명시적 로컬 테스트 모드만 허용, 화면에 테스트 표시 |
| Professor / Developer / 코드 테스트 / Doctor / Counselor | 공유 모델의 System Prompt 기반 선택형 Skill |
| ModelProvider / AssistantEngine / OpenClaw 어댑터 인터페이스 | 구현 |
| OpenAI Responses API | 소유자용 구현, 기본 비활성화, 실제 키 호출 미검증 |
| OpenAI 비용 정책 / 사용량 / 호출 동의 | 서버 단가·예산 설정, DB 예약/정산, 화면 구현 |
| vLLM 직접 Provider | 미구현 |
| OpenClaw HTTP 대화 / 연결 테스트 | 로컬 소유자 전용 구현, 실제 PC 연결 미검증 |
| Permission Gate | 전체 실행 거부 인터페이스만 구현, 승인 UI/실행기 미구현 |
| Docker Compose / 자동 테스트 / CI | 구성, 실행 결과는 PR 및 Actions 확인 |
| 장기 기억 | SQLite 후보/확인, 출처·수정·삭제·만료, 키워드 검색, 채팅 반영 |
| 학습 데이터 | 제한된 샘플 가져오기, 압축 JSONL·카드, 3단계 검수·승인 |
| 학습 Worker | opt-in offline SFT/QLoRA 코드·큐·loss 비교, 실제 GPU 실행 미검증 |
| 임베딩 RAG / 일반 문서 업로드 / 회의 / PC 제어 / DPO·GRPO | 후속 단계 |

기본 대화 엔진은 ProviderAssistant이며, 설정한 로컬 소유자는 OpenClaw로 전환할 수 있습니다.
설정만으로 연결 성공을 표시하지 않습니다. [로컬 OpenClaw 안내](docs/openclaw-local.md)를 참조하세요.
자동 모델 라우팅, 스트리밍, 사용자별 API 키 등록과 Provider 장애 대체는 후속 구현입니다.
OpenAI는 소유자가 서버 키·단가·예산을 설정하고 요청별 동의한 경우에만 호출합니다.
Mock 또는 유료 API로 자동 대체하지 않습니다. [OpenAI 설정 안내](docs/openai-provider.md)를 참조하세요.

## 로컬 실행 (WSL2 / Python 3.11 이상)

    git clone https://github.com/wjdwodhks35/Role-AI.git
    cd Role-AI
    git switch dev
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e ".[test]"
    cp .env.example .env

.env에서 ROLE_AI_BOOTSTRAP_EMAIL과 12자 이상의
ROLE_AI_BOOTSTRAP_PASSWORD를 **직접 설정**하세요. 기본 계정이나 기본 비밀번호는 없습니다.
최초 시작 시에만 해당 계정이 생성되고, 이미 있는 계정의 비밀번호/권한은 바꾸지 않습니다.
생성 후 부트스트랩 이메일과 비밀번호를 .env에서 제거하고 재시작하세요.

    uvicorn role_ai.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers

브라우저에서 http://localhost:8000 에 접속합니다. 회원가입을 직접 테스트하려면
ROLE_AI_REGISTRATION_ENABLED=true를 임시 설정하세요.
환경변수 변경은 재시작 후 적용됩니다. 현재 설정 UI에는 API 키 입력 기능이 없습니다.

### Ollama

Ollama를 별도로 실행하고 .env의 ROLE_AI_OLLAMA_MODEL과 같은 모델을 설치하세요.
기본 모델명은 qwen2.5:7b지만 8GB GPU에서의 실제 적합성은 설치 환경에서 검증해야 합니다.
여러 대형 모델을 동시에 올리는 자동화는 없습니다.
UI의 **연결 테스트**는 서버 및 모델 설치 여부를 확인하며, 추론/GPU 상태의 정상 동작까지 보증하지 않습니다.
WSL2와 Windows 간 네트워크 주소는 실행 위치에 맞게 설정해야 합니다.

### 모델 없이 테스트

    ROLE_AI_DEMO_ENABLED=true
    ROLE_AI_DEFAULT_PROVIDER=mock

Mock은 입력을 되돌려 주는 테스트 응답입니다. 실제 AI 서비스가 아닙니다.
공개 모드에서는 Mock 활성화가 설정 검증으로 차단됩니다.

## Docker Compose

.env와 초기 계정을 먼저 설정한 후:

    docker compose up --build -d
    docker compose logs -f api

FastAPI만 실행합니다. SQLite는 named volume에 저장됩니다.
DB 서버/OpenClaw/Ollama/학습 Worker 컨테이너는 포함하지 않습니다.
학습 Worker는 승인 후 WSL2의 기존 GPU 환경에서 별도로 실행합니다.
Ollama는 기존 호스트 환경을 사용하며 Compose에서는
http://host.docker.internal:11434로 연결합니다.
호스트 Ollama가 해당 네트워크에서 접근 가능한지 별도 확인하고, 공용 인터넷에 노출하지 마세요.
vLLM 환경을 변경하거나 컨테이너화하지 않습니다.
Compose의 서비스 포트는 **127.0.0.1:8000**에만 바인딩됩니다.

## 테스트

    ruff check role_ai tests
    python -m compileall -q role_ai
    node --check role_ai/static/app.js
    pytest -q

테스트는 임시 SQLite와 Mock HTTP transport를 사용하며 외부 API 키/GPU가 필요하지 않습니다.
CI는 Python 테스트, 정적 검사, JavaScript 구문 검사, Compose 유효성 검사,
컨테이너 빌드 및 HTTP smoke test를 수행합니다.
이 검증은 실제 Ollama/OpenClaw/OpenAI 연결 또는 브라우저 E2E 검증을 대신하지 않습니다.

## 데이터와 권한

- 비밀번호는 salt와 PBKDF2-SHA256 600,000회로 해시합니다.
- 세션 원문은 HttpOnly/SameSite 쿠키에만 전달하고 DB에는 SHA256 해시를 저장합니다.
- 쓰기 API는 세션별 CSRF 토큰과 요청 Origin을 검사합니다.
- 사용자 ID는 요청 본문을 신뢰하지 않고 서버 인증 결과에서 가져옵니다.
- 다른 사용자의 대화 조회/쓰기에는 404를 반환합니다.
- API 키/토큰은 서버 환경변수의 SecretStr로만 관리하고 API 응답으로 반환하지 않습니다.
- .env, DB, 녹음, 모델 가중치, 가상환경, 캐시는 Git과 Docker 빌드에서 제외합니다.
- JSON 요청 본문은 64KiB, 메시지는 4,000자, 대화는 500턴으로 제한합니다.
- 대화 기록은 최근 20개 메시지만 모델 컨텍스트로 전달합니다.
- 대화 동시 쓰기는 revision 검사로 충돌을 거부합니다.
- 대화 엔진의 임의 도구 실행, 서버 파일 접근, shell, 이메일, 일정 작성은 제공하지 않습니다.
- 소유자가 승인한 Python unittest는 별도 격리 컨테이너에서 실행할 수 있습니다. 기본 꺼짐이며 [준비·사용법](docs/code-execution.md)을 확인하세요.
- 인증은 IP당 분당 10회, 대화/연결 테스트는 사용자당 분당 20회로 제한합니다.
  프로세스 내부 제한이므로 다중 Worker/공개 배포 전 Redis 및 프록시 정책으로 교체해야 합니다.

현재 공개 서비스 배포는 계획하지 않습니다. Windows/WSL2 개인용 환경에서 개발하고
향후 Linux 서버 구축 후 별도 배포합니다. 공개 전 HTTPS/신뢰할 프록시/분산 Rate Limit,
감사 로그, 데이터 보존/삭제 정책과 백업을 구현·검증해야 합니다.
ROLE_AI_MODE=public은 Secure 쿠키를 요구하지만 이것만으로 공개 서비스 운영 준비가 끝나지 않습니다.
Phase 1 DB는 자동 create_all로 초기화합니다. 스키마 변경 전 Alembic 등 마이그레이션을 도입하세요.
실행 중 DB를 단순 복사하지 말고 SQLite backup API로 일관된 백업을 만들고 복원 테스트를 수행하세요.

## 구조

    role_ai/
      main.py          # 인증·대화·상태 API와 앱 factory
      config.py        # 환경변수와 안전한 설정 기본값
      database.py      # SQLite 연결
      models.py        # 사용자 / 인증세션 / 대화 / 메시지
      repositories.py  # 사용자 범위 데이터 접근
      security.py      # 비밀번호 / 세션 해시 / Rate Limit
      body_limit.py    # JSON 요청 크기 제한
      providers.py     # Provider 계약 / Ollama / Mock / 미구현 Provider
      assistant.py     # 비서 계약 / 기본 비서 / OpenClaw 어댑터 경계
      skills.py        # 전문 역할 프롬프트
      permissions.py   # 실행 거부 기본 정책
      static/          # 한국어 UI
    tests/             # 인증 / 격리 / Provider / 경계 테스트
    docs/              # 설계와 후속 개발 계획

## Git Flow

기존 main의 초기 커밋을 유지합니다. dev는 해당 커밋에서 생성하고
Phase 1은 feature/project-bootstrap에서 개발합니다.
PR 대상은 dev이며 사용자 승인 없이 병합하지 않습니다.
main은 검증된 릴리스만 반영합니다. 후속 기능은 독립적인 feature/*,
버그 수정은 fix/* 브랜치를 사용합니다.
롤백은 승인된 revert PR로 수행하고 DB 변경은 별도 호환성/복원 검증을 합니다.

다음 계획은 [docs/architecture.md](docs/architecture.md) 및
[docs/roadmap.md](docs/roadmap.md)를 참조하세요.
\n## 기억과 학습 관리\n\n채팅 화면의 기억 관리·학습 데이터 버튼을 사용합니다. 학습 관리는 로컬 소유자 전용입니다.\n[기억·학습 실행 안내](docs/memory-learning.md)에 파일 구조, 제한, 검수·승인, Worker 설정과 미검증 범위를 정리했습니다.\n개인 기억을 자동으로 학습에 사용하거나 전체 Hugging Face 데이터셋을 다운로드하지 않습니다.\n
로컬 텍스트·Markdown 문서 보관/검색 및 무료 문서 대화는 [문서 검색 안내](docs/local-document-rag.md)를 참고하세요. 유료 API/원격 임베딩을 추가로 사용하지 않습니다.

PDF 텍스트 추출 및 Ollama 의미 검색은 [설정·제한 안내](docs/pdf-semantic-rag.md)를 참고하세요. 기본 임베딩 모델은 미설정이며 유료 호출이나 자동 모델 다운로드는 없습니다.

AI가 주제별 데이터셋을 자동 탐색·선별·수집하고 로컬 검수하는 [자동 수집 Worker](docs/automatic-collection.md)를 추가했습니다. 사용자 최종 승인 전에는 학습하지 않습니다.

자동 수집 데이터의 최종 학습 판단을 GPT에 위임하는 [ChatGPT 자동 허용 모드](docs/chatgpt-auto-approval.md)를 추가했습니다. 기본 꺼짐이며 명시적 비용·외부 전송·범위 설정이 필요합니다.

최대 VRAM·시스템 여유·작업별 추정치를 설정하는 [GPU 메모리 설정](docs/gpu-memory-settings.md)을 추가했습니다. 작업 전 검사이며 시스템 전체의 강제 VRAM 제한은 아닙니다.

전체 샘플 검수·고정 질문 평가·배포 후보 버전·통합 작업 현황: [설명](docs/model-quality-lifecycle.md). 후보 선택은 채팅 모델을 자동 변경하지 않습니다.


## 운영 기능

- [오프라인 백업·검증·새 경로 복원](docs/backup-restore.md): 기존 데이터 덮어쓰기 없음.
- [DB 버전·마이그레이션](docs/migrations.md): 기존 DB는 중지·백업 후 업그레이드.
- [Worker 관리](docs/workers.md): heartbeat, 실제 학습 스텝, 단계별 취소, 승인 유지 자원 대기 재시도,
  OS 잠금 확인 후 중단 처리와 재승인. 사용자 서버/GPU의 전체 흐름은 별도 검증해야 합니다.

기존 DB는 [마이그레이션](docs/migrations.md)으로 최신 버전까지 업그레이드한 후 API·Worker를 시작하세요.


## 요구사항 분석 전용 평가

[평가 사용법·점수 정의](docs/requirements-evaluation.md): 합성 기본 10개 사례 또는 직접 등록한 정답으로
의도·작업 순서·제약·누락 입력·확인 필요 여부·결과물을 평가합니다. 직접 입력 채점과 명시적으로 승인한
로컬 Ollama 분석을 지원합니다. 정답은 모델에 보내지 않으며 실제 작업 실행·자동 역할 배정은 하지 않습니다.
최신 DB는 v3입니다. 기존 v1/v2 DB는 중지·백업 후 오프라인 업그레이드가 필요합니다.

## 대화 요청 분석 미리보기

[사용법과 적용 범위](docs/request-analysis.md): 로컬 소유자가 메시지를 Ollama로 분석하면 목적·작업 순서·제약·누락 입력·확인 질문과 추천 역할을 표시합니다.
추천 역할은 사용자가 적용할 때만 선택값을 바꾸며, 메시지 전송·코드 실행·학습·유료 호출은 시작하지 않습니다.
분석 결과는 참고용이며 실제 모델의 요구사항 파악 정확도는 별도 검증이 필요합니다.
