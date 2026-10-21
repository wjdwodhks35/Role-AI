# 로컬 서버 준비 상태 진단

이 진단은 명령을 실행한 호스트만 조사합니다. Work의 결과를 사용자 PC 또는 별도 Linux 서버 상태로 해석하지 마세요.
접속 대상·계정·인증 수단이 제공되지 않은 서버에 자동으로 접속하지 않습니다.

## 기본 진단

프로젝트의 기존 가상환경을 활성화한 후 저장소 디렉터리에서 실행합니다.

```bash
python -m role_ai.preflight
```

Python 3.11 이상과 프로젝트 기본 의존성(httpx)이 필요합니다. 설정 파일을 로드하지 않으므로 초기 소유자나 API 키가 없어도 실행됩니다.
명령은 JSON을 표준 출력으로 내보냅니다. 기본 진단은 모델 HTTP 요청을 하지 않습니다.

- OS·배포판·WSL 여부, CPU 아키텍처, 메모리·디스크 여유
- 현재 Git 커밋·브랜치·변경 존재 여부 (변경된 파일명은 출력하지 않음)
- .env의 존재·심볼릭 링크·그룹/기타 권한 여부 (설정 내용은 읽지 않음)
- DB·WAL·SHM·artifact 경로의 존재와 파일 크기 (DB를 열거나 폴더 내부를 나열하지 않음)
- nvidia-smi가 제공하는 GPU 이름·드라이버·전체/사용 메모리
- 로컬 Docker daemon·Compose 접근과 실행 중 컨테이너 개수
- 8000·11434 포트의 수신 여부 (해당 서비스의 정상 동작을 의미하지 않음)

Docker는 현재 CLI context를 확인합니다. SSH/TCP 등 원격 daemon은 접속하지 않고 `remote_or_unknown_context`로 표시합니다.
Unix/npipe endpoint는 로컬 소켓 접근임을 뜻하며 소켓 뒤의 실제 서버 위치까지 보장하지 않습니다.
`missing`은 명령을 찾지 못한 상태, `unavailable`은 권한·기동·응답 등을 확인하지 못한 상태입니다.
GPU 조회 실패는 GPU 부재 판정이 아니며 Docker 조회 실패도 설치 실패 판정이 아닙니다.
메모리 값은 호스트가 제공하는 /proc/meminfo 기준으로, 컨테이너 제한과 다를 수 있습니다.

실제 데이터 위치가 다르면 **알고 있는 경로**를 지정하세요. 상대 경로는 project-dir 기준, 절대 경로는 그대로 사용합니다.

```bash
python -m role_ai.preflight --project-dir /path/to/Role-AI \
  --database /actual/data/role-ai.db --storage /actual/data/artifacts
```

Compose named volume은 기본 `./data`와 별개입니다. 이 진단은 volume 내부나 모델 폴더를 탐색하지 않습니다.
실행 중인 앱과 Worker의 데이터 위치·사용 volume·모델 경로를 먼저 확인해야 합니다.
존재하는 DB는 WAL과 artifact를 함께 고려해 서비스 중지 후 일관된 백업을 계획하세요.
[백업·복원](backup-restore.md), [오프라인 마이그레이션](migrations.md)을 따릅니다.

## Ollama 연결과 모델 설치 확인

이 단계는 지정한 로컬 Ollama로 HTTP 요청을 보냅니다. 프로젝트 .env에서 URL·모델명을 자동으로 읽지 않습니다.
설정한 정확한 모델명을 직접 지정하세요. 아래 모델명은 예시이며 다운로드하지 않습니다.

```bash
python -m role_ai.preflight --ollama-check --model qwen2.5:3b
```

`/api/version`, `/api/tags`, `/api/show`에서 연결·설치·completion 지원·cloud 메타데이터를 확인합니다.
HTTP 루프백(127.0.0.1/localhost/::1)과 Compose의 host.docker.internal만 허용합니다.
리디렉션·프록시 환경변수는 사용하지 않으며 응답 크기는 256KiB 이하로 제한합니다.
오류 원문·설치 모델 목록·모델명·URL·프롬프트·모델 응답 본문은 보고서에 출력하지 않습니다.
cloud 메타데이터 확인은 네트워크 격리 자체를 제공하지 않습니다.

API 컨테이너에서 호스트 Ollama를 검사하려는 경우 컨테이너 안에서 명령을 실행해야 같은 네트워크 경로를 검사합니다.
현재 실행 위치에서 실제로 접근 가능한 주소를 지정하세요.

```bash
python -m role_ai.preflight --ollama-check \
  --ollama-url http://host.docker.internal:11434 --model qwen2.5:3b
```

## 명시적인 추론 1회

아래 옵션은 별도 동작입니다. 설치된 로컬 completion 모델로 짧은 연결 검사 요청을 한 번 생성합니다.
모델을 메모리에 올려 GPU/CPU를 사용하고 Ollama 로그나 런타임 캐시에 영향을 줄 수 있습니다.
Ollama의 기존 keep-alive 정책을 사용하며 강제 unload를 요청하지 않습니다.

```bash
python -m role_ai.preflight --ollama-check --model qwen2.5:3b --ollama-generate
```

모델이 없거나 cloud 모델이면 `blocked`로 표시하고 추론하지 않습니다.
모델 다운로드·설치·학습·코드 실행·OpenAI 호출·서비스 시작·설정 변경은 수행하지 않습니다.
`response_received`는 선택한 모델의 완료된 비어 있지 않은 응답 수신만 뜻합니다.
`quality_verified`는 항상 false입니다. 요구사항 분석 정확도·검색 품질·GPU 학습 성공을 증명하지 않습니다.
자동 복구·재시도·대체 모델 전환 없이 실패 상태를 보고합니다.

## 다음 확인 순서

1. 사용자 서버에서 기본 진단 결과와 실제 데이터 위치를 확인합니다.
2. 기존 데이터가 있으면 서비스 중지·일관된 백업·복원 검사를 선행합니다.
3. Ollama 설치 확인 후 필요할 때만 명시적 추론 검사를 실행합니다.
4. Role-AI를 127.0.0.1에서 실행하고 로그인·문서·기억·의미 검색을 실제 브라우저로 확인합니다.
5. OpenClaw는 같은 호스트 직접 실행 경로와 도구 전체 차단을 확인한 뒤 대화를 검사합니다.
6. GPU·로컬 기반 모델·의존성·승인 데이터·고정 평가 묶음이 갖춰진 별도 환경에서만 소규모 학습을 검증합니다.

진단 명령의 종료 코드 0은 JSON 보고서 생성 성공을 뜻하며 서버 준비 완료 판정이 아닙니다.
`unverified`와 `next_steps`를 함께 확인하세요. 이 기능으로 DB 스키마는 변경되지 않습니다.
