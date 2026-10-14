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
| Professor / Developer / Doctor / Counselor | 공유 모델의 System Prompt 기반 선택형 Skill |
| ModelProvider / AssistantEngine / OpenClaw 어댑터 인터페이스 | 구현 |
| OpenAI / vLLM 실제 연결 | 미구현, API 키를 설정해도 호출하지 않음 |
| OpenClaw HTTP 대화 / 연결 테스트 | 로컬 소유자 전용 구현, 실제 PC 연결 미검증 |
| Permission Gate | 전체 실행 거부 인터페이스만 구현, 승인 UI/실행기 미구현 |
| Docker Compose / 자동 테스트 / CI | 구성, 실행 결과는 PR 및 Actions 확인 |
| RAG / 파일 업로드 / Worker / 회의 / PC 제어 / 학습 | 후속 단계 |

기본 대화 엔진은 ProviderAssistant이며, 설정한 로컬 소유자는 OpenClaw로 전환할 수 있습니다.
설정만으로 연결 성공을 표시하지 않습니다. [로컬 OpenClaw 안내](docs/openclaw-local.md)를 참조하세요.
자동 모델 라우팅, 스트리밍, 비용 한도, 암호화된 사용자별 API 키 등록,
자동 재시도 및 Provider 장애 대체는 후속 구현입니다.
Mock으로 자동 대체하지 않으며 유료 API를 호출하지 않습니다.

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
Worker/DB 서버/OpenClaw/Ollama 컨테이너는 아직 포함하지 않습니다.
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
- 도구 실행, 서버 파일 접근, shell, 이메일, 일정 작성은 제공하지 않습니다.
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
