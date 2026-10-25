# Role-AI 프로젝트 지도

2026-10-25 파일·README·CI 기준의 정적 색인이다. 경로·명령 변경 시 함께 갱신한다. 현재 원본 코드와 실행 결과를 우선하며 전체 소스 대신 목차로 사용한다.

| 경로 | 역할 |
| --- | --- |
| `role_ai/main.py` | FastAPI `create_app`, 인증·대화 API 및 라우터 조립 |
| `role_ai/config.py` | 환경 설정; 비밀값은 `.env`에서 읽고 출력하지 않음 |
| `role_ai/models.py` | SQLAlchemy 모델 |
| `role_ai/database.py` | DB 연결·세션 |
| `role_ai/migrations.py` | 스키마 마이그레이션 |
| `role_ai/repositories.py` | 대화 저장·동시성 |
| `role_ai/assistant.py` | AssistantManager, 대화용 OpenClaw 어댑터 |
| `role_ai/providers.py` | Ollama·Mock 제공자 |
| `role_ai/documents.py` | 문서 업로드·검색 API |
| `role_ai/semantic.py` | 의미 검색 |
| `role_ai/memory.py` | 기억 저장·검색 |
| `role_ai/workers.py` | 작업 상태·heartbeat·취소 |
| `role_ai/code_execution.py` | 승인 기반 코드 실행 |
| `role_ai/rlaif.py` | 로컬 학습 흐름 |
| `role_ai/collection.py` | 수집 라우터·worker main |
| `role_ai/preflight.py` | 배포 사전 점검 CLI |
| `role_ai/static/app.js` | 기존 웹 UI 동작 |
| `role_ai/static/index.html` | 웹 UI 마크업 |
| `tests/` | 기능별 Python 테스트; 파일명으로 관련 검사 선택 |
| `qa/` | 요구사항 회귀·웹 인증·키보드 검사 |
| `tools/windows-launcher/` | Windows CMD/PowerShell·WSL 서비스 시작·전용 창 |
| `tools/codex/run_logged.py` | 로컬 원본 보관·보수적 로그 축약 |
| `tools/codex/tests/` | Codex 보조 도구의 표준 unittest 검사 |
| `docs/CODEX_OPTIMIZATION.md` | 적용 범위·보안·검증·측정 결과 |
| `pyproject.toml` | Python >=3.11, FastAPI·SQLAlchemy, pytest·ruff 설정 |
| `.github/workflows/ci.yml` | 기존 CI 명령의 기준 |
| `compose.yaml` | 개발용 API·선택적 collector 정의 |

개발 명령은 저장소 루트 기준이다. 설치된 환경에서 필요한 것만 실행한다.

- API: `uvicorn role_ai.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers`
- 관련 Python 테스트: `python3 tools/codex/run_logged.py -- python3 -m pytest tests/test_api.py -q` (대상 파일은 변경에 맞춰 선택)
- 기존 정적 검사: `ruff check role_ai tests qa`, `python3 -m compileall -q role_ai`, `node --check role_ai/static/app.js`
- UI 회귀: `node qa/check_web_auth.cjs`, `node qa/check_chat_keyboard.cjs`
- 보조 도구 검사: `python3 tools/codex/run_logged.py -- python3 -m unittest discover -s tools/codex/tests -v`
- 프로젝트 전체 테스트는 `python3 -m pytest -q`; 의존성이 준비된 경우 필요에 따라 실행한다. 출력이 길면 위 래퍼로 감싼다.

Windows에서는 설치된 `python`을 사용한다. 운영 `/root/Role-AI`와 개발 저장소를 구분한다. 운영 시작은 기존 Compose 세 파일을 사용하는 Windows 실행 도우미를 기준으로 하며, 개발용 빌드 예제를 운영에 그대로 적용하지 않는다.
