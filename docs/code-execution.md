# 소유자 승인 코드 테스트 실행

`코드 테스트` Skill은 테스트 작성·실패 분석을 담당합니다. 실제 실행은 대화 엔진의
도구 호출이 아니라 별도 소유자 승인 화면과 FastAPI API가 처리합니다.
OpenClaw 전체 도구 차단은 유지합니다. 기본 비서에서도 같은 실행 화면을 사용할 수 있습니다.

## 현재 지원

- Python 3.12 표준 라이브러리와 `unittest`, 코드 1개(`submission.py`)와 테스트 1개
  (`test_submission.py`), 각각 최대 16,000자. 테스트에서는 `from submission import ...` 사용.
- `POST /api/code-execution/run`: 로그인·CSRF·로컬 소유자·이번 요청의 `approved=true` 필요.
- 일회성 Linux Docker 컨테이너, 128MiB 메모리/동일 swap 합계, CPU 0.5개, PID 32개,
  기본 실행 제한 10초(설정 범위 1~30초), 출력 최대 32KiB. 설치·자동 다운로드 없음.
- 네트워크 없음, 읽기 전용 루트/코드, 비관리자 UID 65534, 전체 capability 제거,
  no-new-privileges, 기본 seccomp 필요, IPC 공유 없음, 16MiB 임시 RAM 파일시스템.
- 프로젝트·DB·`.env`·모델·Docker 소켓·사용자 디렉터리는 실행 컨테이너에 마운트하지 않음.
  앱 환경 변수와 Docker 클라이언트 인증 설정도 전달하지 않음.
- 성공·실패 여부를 stdout의 'OK' 문자열로 판단하지 않고 Docker 종료 코드를 표시.
  상태는 `completed`, `timeout`, `output_limit`, `memory_limit`이며 종료 코드와 함께 표시.
  테스트 없음은 종료 코드 5, 일반 unittest 실패/문법 오류는 0이 아닌 종료 코드.
  사용자 코드가 결과를 조작하거나 빈약한 테스트를 작성할 수 있어 종료 코드 0은 품질 보증이 아님.
- 결과는 요청 응답과 현재 브라우저 화면에만 존재. DB에 원문/출력을 저장하지 않음.
  결과 첨부 후 보내기를 눌러야 대화 기록에 저장되고 선택된 모델에 전달됨.

## 준비와 설정

기본은 비활성화입니다. Linux/WSL 호스트에 Docker CLI와 Linux Docker 데몬이 필요합니다.
Docker는 강한 VM 보안 경계가 아니므로 본인이 검토한 코드만 실행하고, 별도 테스트용
Linux 환경이나 rootless Docker 사용을 권장합니다. rootless에서는 cgroup 자원 제한도
실제로 지원해야 합니다. Docker 접근 권한 자체가 높은 권한이라는 점을 고려하세요.

현재는 **API를 동일 Linux 호스트에서 직접 실행하는 구성**만 지원합니다.
기본 Compose API는 Docker CLI/소켓이 없으므로 이 기능을 켜지 마세요.
이를 해결하려고 API 컨테이너에 호스트 Docker 소켓을 마운트하지 마세요.
별도 실행 Worker 서비스 및 Compose 연동은 후속 작업입니다.
기존 Docker·vLLM·모델 환경은 자동 변경하지 않습니다.

관리자가 명시적으로 `python:3.12-slim` 이미지를 준비하고 RepoDigest를 확인합니다.
런타임은 고정 digest가 붙은 공식 Python 이미지 형식만 허용하고 `--pull=never`로 실행합니다.
아래 준비 명령은 이미지가 아직 없으면 다운로드합니다. 앱은 이 작업을 자동 수행하지 않습니다.

```bash
docker pull python:3.12-slim
docker image inspect python:3.12-slim --format '{{json .RepoDigests}}'
```

기존 `.env`의 다른 설정을 유지하면서 다음을 설정합니다. `<실제 digest>`는 위 명령의
`sha256:` 뒤 64자리 값으로 교체합니다. 키나 비밀을 넣는 항목이 아닙니다.

```dotenv
ROLE_AI_CODE_EXECUTION_ENABLED=true
ROLE_AI_CODE_EXECUTION_IMAGE=python:3.12-slim@sha256:<실제 digest>
ROLE_AI_CODE_EXECUTION_SOCKET=/var/run/docker.sock
ROLE_AI_CODE_EXECUTION_TIMEOUT_SECONDS=10
```

rootless Docker라면 `CODE_EXECUTION_SOCKET` 대신 전체 이름인
`ROLE_AI_CODE_EXECUTION_SOCKET`에 실제 Unix 소켓 경로(예: `/run/user/1000/docker.sock`)를 설정합니다.
원격 TCP Docker 및 호스트 프로세스 대체 실행은 지원하지 않습니다.
메모리·swap·CPU·PID 제한 또는 seccomp가 확인되지 않으면 실행하지 않습니다.

```bash
uvicorn role_ai.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers
```

소유자로 로그인 → **코드 테스트 실행** → 코드/테스트 검토 → 실행 동의 → 실행.
입력을 수정하면 기존 동의를 해제합니다. 결과 분석이 필요하면 **현재 대화 입력에 결과 첨부**를
누르고 로그를 확인한 뒤 보내세요. 긴 결과는 필요한 부분만 복사해야 합니다.
설정 활성화 표시는 Docker나 모델 연결 검증 완료를 뜻하지 않습니다.

## 정리·오류 처리와 운영 한계

정상 종료·테스트 실패·시간/출력 초과·처리 예외 후 해당 작업의 컨테이너와 임시 파일을 제거합니다.
정리가 실패하면 오류를 반환하고 해당 API 프로세스의 추가 실행을 차단합니다.
현재는 단일 API 프로세스에서 작업 하나씩 실행합니다. 외부 큐·실행 이력·취소 UI는 없습니다.
브라우저를 닫아도 이미 승인한 작업은 제한 시간까지 계속 처리됩니다.

API 강제 종료/호스트 장애/데몬 장애 때 컨테이너가 남을 수 있습니다. 재시작 전에 관리자가
아래 목록을 확인하고 **이번 Role-AI 테스트에서 생성한 것으로 확인된 컨테이너만** 정리하세요.
임의의 전체 컨테이너/volume 삭제 명령을 사용하지 마세요.

```bash
docker ps -a --filter label=role-ai.code-execution=true
# 위에서 확인한 해당 작업 ID만 지정
docker rm -fv <확인한-role-ai-test-컨테이너-ID>
```

실제 사용자 서버의 Docker/WSL 제한 동작과 GPU/모델은 별도 실환경 검증 대상입니다.
이 실행 기능은 GPU·네트워크·추가 패키지를 사용하지 않습니다.

## 검증

Excel 계획에는 권한/CSRF/명시적 승인, 호스트 실행 차단, 자원 제한 확인, 출력·시간 제한,
정리 실패 시 차단을 검사하는 단위 테스트를 포함합니다. 모사 테스트는 실제 Docker 증거가 아닙니다.
CI의 `code-execution` 작업은 명시적으로 이미지를 준비하고 실제 Docker에서 성공/실패,
0개 테스트/문법 오류/시간 초과/과다 출력/메모리 한계와 네트워크·파일·환경 격리·정리를 검사합니다.
`python qa/check_code_execution.py`는 관리자가 이미지를 준비하고
`ROLE_AI_CODE_EXECUTION_IMAGE`를 설정한 별도 테스트 환경에서만 실행하세요.

참고: [Docker 실행 옵션](https://docs.docker.com/engine/containers/run/),
[Docker 보안 경계](https://docs.docker.com/engine/security/).
