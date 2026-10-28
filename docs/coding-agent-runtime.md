# Coding Agent runtime · 개발용 구현

이번 범위는 Coding Agent의 탐색·계획·수정·격리 테스트·오류 분석/재수정·체크포인트·
중단/재개와 최소 경험 기록이다. v1.1 원본 기획서는 변경하지 않는다. Preview Lab·멀티모달·
전략 검색/최적화·파인튜닝·운영 배포는 연결하지 않는다.

## 현재 동작

- 기존 인증/CSRF를 사용하는 로컬 소유자 전용 `/api/coding` API와 Coding Agent 화면.
- 작업 등록은 실행과 분리한다. 별도 `python -m role_ai.coding_agent` Worker가 승인된 작업만 실행한다.
- 로컬 Ollama가 JSON action 하나씩 계획/list/read/search/patch/test/finish를 선택한다.
  잘못된 JSON, 허용하지 않은 action·파일, 테스트 미실행 상태의 finish는 성공 처리하지 않는다.
- 모델 출력은 명령 승인이 아니다. 등록된 저장소, 요청 시 승인한 파일/명령/예산을 서버가 검사한다.
- 각 patch는 pinned base commit에서 별도 worktree를 구성하고 이전 checkpoint 코드를 복원하여
  적용한다. 원본 dirty/untracked 파일을 보존한다. 바이너리는 수정하지 않고 snapshot에 보존하며
  모델에 보내지 않는다. 수정 가능한 파일은 제한된 텍스트 확장자이며 기존 테스트는 읽기 전용이다.
- 테스트는 정제된 소스 복사본과 고정 wrapper를 일회성 Docker에 넣어 수행한다.
  unittest/pytest 두 템플릿만 지원하며 shell/임의 명령·다운로드·호스트 대체 실행은 없다.
- 테스트 0개/보고서 없음/종료 코드 불일치/timeout/OOM/출력 초과는 통과가 아니다.
  현재 revision에서 실제 통과한 결과가 있어야 completed/PR 후보 보고서를 생성한다.
  이는 테스트의 충분성이나 코드 안전성의 완전한 증명은 아니다.

## 개발 환경에서 켜기

기본은 `coding_enabled=false`다. 설치·서비스 시작·기존 운영 환경 변경을 자동 실행하지 않는다.
API와 Worker는 같은 개발 전용 root와 저장소 등록, 같은 로컬 Docker 파일시스템을 사용해야 한다.
운영 체크아웃/운영 볼륨을 등록하지 않는다. 개발용 프로세스 환경에만 다음 항목을 설정한다.

```text
ROLE_AI_CODING_ENABLED=true
ROLE_AI_CODING_ROOT=/별도/개발전용/사유/coding-jobs
ROLE_AI_CODING_REPOSITORIES={"example":"/승인된/개발/저장소"}
ROLE_AI_CODING_IMAGE=승인된로컬이미지@sha256:<실제64자리digest>
```

기존 ROLE_AI_OLLAMA_URL/MODEL, ROLE_AI_STORAGE_DIR, ROLE_AI_CODE_EXECUTION_SOCKET 설정을 사용한다.
이미지는 로컬에 설치된 Linux 이미지이며 digest가 고정되어야 한다. 자동 pull은 하지 않는다.
간단한 unittest 저장소는 Python이, pytest 저장소는 pytest와 해당 프로젝트 의존성이 이미지에
미리 있어야 한다. Role-AI 자체의 전체 테스트에는 test 의존성과 git 등도 필요하다.
이미지 준비·패키지 설치·모델 설치가 필요하면 해당 개발 환경에 대한 승인을 먼저 확인한다.

Docker daemon은 Memory/Swap/CPU/PID 제한과 seccomp를 지원해야 한다. runner에는
network=none/read-only root/non-root/cap-drop/no-new-privileges·메모리/CPU/PID/출력/시간 제한을
적용하며 Docker socket·운영 볼륨·GPU·Git 메타데이터·호스트 자격증명을 넘기지 않는다.
API 컨테이너에 host socket을 새로 mount하는 Compose 변경은 하지 않는다. 별도 개발 Worker가
호스트에서 Docker를 실행하는 구성을 기준으로 한다. daemon과 Worker의 경로 namespace가 다르면
bind mount를 사용할 수 없으므로 같은 파일시스템의 승인된 개발 실행 환경을 사용한다.

화면에서 등록 저장소·요청·수정 파일·예산·명령을 선택하고 작업 승인을 체크한다.
선택적 최소 전략 기록 동의는 따로 받는다. Worker 시작은 다음과 같다.

```text
python -m role_ai.coding_agent --once
python -m role_ai.coding_agent
```

첫 명령은 현재 대기 작업을 처리한 뒤 종료하고, 두 번째는 대기 작업을 주기적으로 처리한다.
운영 서비스에 자동 등록하거나 기존 서비스·Cloudflare·OpenClaw 설정을 수정하지 않는다.

## 예산과 재시도

작업별 budget은 생성 시 고정한다. UI에서 주요 항목을 설정하고 API에서는 아래 모든 항목을
서버 hard cap 안에서 설정할 수 있다. 모델은 예산을 변경할 수 없다.

| 항목 | 기본 / 상한 |
|---|---|
| 단계·모델 호출 | 각각 24 / 128 |
| 실패 후 재수정·동일 오류 | 3 / 10, 동일 오류 2 / 5 |
| 누적 활성 시간 | 1800초 / 14400초 |
| 생성 후 전체 유효기간 | 86400초 / 604800초 |
| 명령 시간 | 30초 / 300초, 남은 활성 시간으로 추가 제한 |
| 입출력 토큰 | 65536 / 524288, 출력 호출당 1024 / 2048 |
| 누적 수정 파일·바이트 | 8 / 16, 65536 / 262144 |
| 명령 RAM·CPU | 256MiB / 2048MiB, 500 / 2000 millicores |

토큰은 호출마다 context 4096+최대 출력량을 보수적으로 예약한다. 실제 사용량보다 빨리 예산을
소진할 수 있다. GPU slot을 얻지 못해 호출하지 않은 경우만 예약을 환불한다.
명령·모델 호출 전에 시간 예약과 pending intent를 저장하므로 crash/unknown 호출이 재개 때
무료 호출이나 예산 초기화가 되지 않는다. 일시중지는 활성 시간에 포함하지 않지만 전체 유효기간은 유지한다.
같은 실패 식별자 반복, 동일 코드+오류 순환, 재시도/단계/시간/토큰 소진은 budget_exhausted로 종료한다.
scope 위반·도구 오류는 fail-closed이며 임의 권한 확대나 자동 패키지 설치로 우회하지 않는다.

## 중단·재개·장애

checkpoint는 사유 디렉터리의 원자적 JSON+checksum이며 snapshot(바이너리는 base64), 고정
기준 commit/등록 정책 hash, 계획, 단계, 코드 revision, 테스트 증거, 예산, pending intent를 담는다.
쓰기와 디렉터리 fsync 뒤에만 저장 완료로 취급한다. checksum은 손상 탐지이지 사용자 인증이 아니다.
기존 SQLite schema는 그대로다. 이 작업 저장소는 기존 DB/학습 artifact backup의 자동 포함 대상이
아니므로 별도 보존·백업이 필요하다. 작업은 사용자당 최대 16개이며 checkpoint 크기는 8MiB,
원본 snapshot은 256개 파일/보존 파일당 1MiB/전체 2MiB, 작업 폴더는 32MiB까지 제한한다.
신규 수정 파일은 기존 256KiB 상한과 모델 action의 더 작은 입력 상한을 유지한다.

pause/cancel은 별도 control 파일로 요청하고 현재 제한된 단계 종료 후 반영한다. pause 요청을
즉시 paused라고 꾸미지 않는다. cancel은 resume으로 취소하지 않는다. Worker 단일 소유 잠금을
획득하지 못하면 이중 실행/재개를 거부한다. 프로세스 재시작 후 running 작업을 자동 재실행하지 않는다.

실행 중 죽은 작업은 pending이 남는다. 사용자는 실제 종료를 확인한 뒤 명시적
acknowledge_unknown을 포함해 resume해야 한다. 테스트 container는 저장된 고유 이름과 label을
검증한 뒤 정리한다. 완료 여부를 추정해 과거 성공 보고서를 만들어 넣지 않는다.
모델 HTTP timeout은 GPU 작업 종료 증명이 아니므로 `.inference-uncertain` 보호 파일을 남긴다.
이 파일이 있으면 기존 대화/임베딩을 포함한 관리 추론도 새로 시작하지 않는다. 운영자가 모델
서버 종료 상태를 확인한 뒤 보호 파일을 정리해야 한다. resume 승인만으로 자동 해제하지 않는다.
Docker 정리 실패의 `runner-cleanup-required`도 실제 labelled container를 점검한 뒤 정리해야 한다.
원인 미확인 상태에서 보호 파일만 지워 재실행하지 않는다. checkpoint 손상/쓰기 실패 시 계속 실행하거나
재개 가능하다고 표시하지 않는다. 삭제/자동 복구로 원본이나 알 수 없는 경로를 덮어쓰지 않는다.

GPU 추론은 기존 cross-process storage lock에 참여하며 관리 경로 동시성은 1이다. 기존 모델을
사용하고 remote-backed Ollama 모델·외부 endpoint·자동 fallback을 거부한다. 다른 프로그램의
GPU 접근까지 제어하는 하드웨어 분할은 아니다. 4060 Ti 8GB의 실제 품질/VRAM은 별도 수동 검증한다.

## 최소 기록·결과

선택적 strategy.jsonl은 식별 hash·phase·상태·횟수·토큰·revision·현재 revision의 실제 테스트
상태만 기록한다. 원문 요청/소스/diff/로그/경로는 넣지 않는다. 검증은 observed일 뿐 학습 적격이나
일반화된 우수 전략이 아니다. 기록 오류는 recorder_gap으로 표시하고 필수 checkpoint와 분리한다.
검색/최적화/학습은 없다. raw source와 테스트 출력은 사유 checkpoint에만 보존한다.

완료 보고서는 summary·누적 diff·변경 파일·실제 테스트 결과·image/revision/container 증거를 제공한다.
자동 git push/PR 병합/배포는 하지 않는다. PR 후보 patch를 검토한 뒤 별도 GitHub 작업으로 반영한다.

## 검증 구분

- pytest의 scripted model/runner는 실제 Git·파일·checkpoint/API를 검사하며 LLM 성능의 증거가 아니다.
- `qa/check_coding_runtime.py`는 scripted model과 실제 Docker unittest로 실패→저장/중단→재개→
  재수정→통과를 검사한다. 사전학습 모델·GPU 학습 없이 CI에서 실행한다.
- 사용자는 실제 설치 모델로 작은 저장소 과제부터 확인한다. 모델 응답 품질/실GPU/실브라우저/
  운영 환경 동작을 자동 단위 검사와 구분한다. 기존 운영 서버는 수정하지 않는다.
