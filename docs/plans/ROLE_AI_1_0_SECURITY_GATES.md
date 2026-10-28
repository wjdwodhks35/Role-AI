# Coding Agent 초기 보안 경계

## 보존

- 기존 SQLite v3·데이터·인증/CSRF·백업 경로를 변경하지 않는다.
- 대화 OpenClaw는 owner-only/loopback/token/로컬 모델/빈 fallback 및 `tools.deny=["*"]`를 유지한다.
  Windows 제어 Gateway와 코딩 실행 경로를 분리한다.
- Compose/named volume/환경변수/Gateway·서비스 변경, 모델 설치/교체와 실제 학습은 별도 승인이다.
- 외부 제공자·유료 API·추가 외부 전송·데이터셋/모델 다운로드를 자동 활성화하지 않는다.

## 내부 라이브러리의 호출 전제

`CodingWorkspace(repository, root, spec)`는 운영자가 등록한 저장소 경로와 사유 작업 루트만 받는다.
경로를 외부 HTTP 요청이나 모델 출력에서 직접 받으면 안 된다. 현 PR은 API/채팅/모델 도구를
노출하지 않는다. 승인 해시는 권한 증명이나 비밀 토큰이 아니다. 향후 Permission Gate는 기존
인증/CSRF/소유권 검사 후 승인 사실을 서버에 기록하고, 사용자·프로젝트·작업·commit·파일 범위·
예산에 연결한 task digest와 정확한 변경 내용의 change digest가 일치할 때만 내부 함수를 호출한다.
모델의 approved 텍스트나 스스로 계산한 digest를 승인으로 취급하지 않는다.

## 파일과 Git

- 원본 저장소는 commit blob 읽기와 Git worktree 등록/해제만 수행한다. dirty/untracked 파일을
  복사하거나 정리하지 않으며 원본 HEAD/브랜치/인덱스를 변경하지 않는다.
- worktree checkout을 생략하고 텍스트 blob을 직접 구성한다. hook/fsmonitor를 차단하며
  smudge/clean/textconv/external diff를 호출하지 않는다. 상속 Git 설정/credentials/proxy를 전달하지 않는다.
- 작업 루트는 소유자 전용이며 원본 저장소와 별도 위치다. 작업 ID 경로를 재사용하지 않는다.
- 작업·변경 승인 해시, 파일 목록, 이전 파일 SHA-256, 파일/전체/수정량/시간 상한을 검사한다.
- 경로 탈출·symlink/hardlink·특수 파일·submodule·binary·추적된 비밀 경로를 거부한다.
  보안/인증/설정/배포 파일은 MVP 수정 대상에서 제외한다. 경로 필터는 완전한 비밀 탐지가 아니며
  향후 외부 전송/PR에는 별도의 내용 검토가 필요하다.
- 적용 전 전체 작업공간과 Git 등록 정보를 확인한다. 부분 쓰기 실패는 failed로 보존·차단한다.
  원본 저장소는 영향을 받지 않는다. 재시도는 새로운 작업공간과 재검토된 승인을 사용한다.

## 실행·산출물

worktree는 보안 sandbox가 아니다. 현 PR은 명령 실행·패키지 설치·모델 추론·push/merge·PR 생성
기능을 제공하지 않는다. 이후 runner는 별도 컨테이너의 정제된 복사본을 사용하며 host socket·
운영 볼륨·Git 메타데이터·자격증명을 전달하면 안 된다. 승인되지 않은 저장소 테스트도 실행하지 않는다.
사유 작업 디렉터리에서 악성 프로세스를 동시 실행하거나 같은 UID의 공격자와 공유하는 것은
지원하지 않는다. 외부 runner 연결 전 이 경계를 별도 검증한다.

diff는 작업 파일 원문을 포함한다. 응답/외부 전송 전에 소유권을 검사하고 사유 저장소에 보관한다.
진행 이벤트의 `executed=false`, `tests_state=not_run`, `progress=null`을 유지하며, heartbeat/자원
미측정을 실제 측정으로 꾸미지 않는다. 6개 registry 역할의 definition_only 상태는 그대로다.
