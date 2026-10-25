# Windows 실행 도우미

Windows 바탕 화면의 `RoleAI-Launcher` 폴더에서 `Start-RoleAI.cmd`를 더블클릭한다. 폴더의 파일은 함께 보관한다. EXE 설치나 관리자 PowerShell은 필요하지 않다. PowerShell 실행 정책은 해당 프로세스에서만 Bypass를 사용한다.

처음에는 기존 운영 설정이 있는 Ubuntu를 찾는다. 여러 Ubuntu에서 발견되면 `settings.json`의 `Distro`에 배포판 이름을 지정한다. 배포판 이름에는 영문·숫자·점·밑줄·하이픈을 사용한다. 운영 경로는 `/root/Role-AI`, PC Gateway 사용자는 `serveradmin`이다.

실행 순서:

1. 설치된 Windows Ollama 앱에 시작을 요청한다.
2. WSL root로 Docker를 시작하고 기존 Compose 세 파일의 API와 대화용 OpenClaw를 기존 이미지로 시작한다.
3. PC Gateway가 정상 응답하면 재사용한다. 아니면 `serveradmin`의 별도 최소화 WSL 창에서 실행한다. **이 창은 닫지 않는다.**
4. Windows에서 API/Gateway 접속과 WSL의 설정된 Ollama 모델을 확인하고 Companion 시작을 요청한다.
5. Role-AI 브라우저를 연다. 오류가 있으면 메인 창에 표시한다.

기존 컨테이너는 기본적으로 재생성하지 않는다. 단, OpenClaw가 현재 API와 다른 컨테이너의 네트워크를 참조하면 OpenClaw만 기존 이미지로 재생성한다. 이미지가 없으면 실패하며 다운로드하거나 빌드하지 않는다. API·데이터 볼륨·토큰·승인 정책·Ollama 주소는 변경하지 않는다.

PC Gateway 로그는 `/home/serveradmin/.openclaw/windows-control-state/launcher/gateway.log`에 저장된다. 다른 프로세스가 18790을 점유하면 종료시키지 않고 실패한다. Companion 실행 요청 후 설정된 노드의 paired/connected 상태를 최대 5회 확인한다. 연결되지 않으면 경고하며, Companion에서 기존 Gateway `ws://127.0.0.1:18790` 연결과 Node mode를 확인한다. 노드 연결은 AI 명령 실행 성공을 의미하지 않는다. Ollama 경고가 나오면 기존 주소/포트와 설치된 모델을 확인한다.

서비스를 시작하지 않는 사전 검사:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Launcher.ps1 -CheckOnly
```

브라우저를 열지 않고 실제 시작을 검사하려면 `-NoBrowser`를 사용한다. `settings.json`의 `OpenBrowser`를 false로 바꿔도 된다. Windows 로그인 자동 실행은 등록하지 않는다.

소스는 `tools/windows-launcher`에서 관리하며 Windows에 배포할 때 전체 파일을 복사한다. `.ps1`은 Windows PowerShell 5.1용 UTF-8 BOM/CRLF, `.cmd`는 CRLF, Linux 스크립트는 LF를 유지한다.
