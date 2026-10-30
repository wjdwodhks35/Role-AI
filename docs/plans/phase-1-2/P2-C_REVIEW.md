# P2-C 공통 계약 기반 로컬 위임

시작 dev: `bba1ba1df1a1c7aead21afc1a354978494ad8916` (P2-B #44 병합). PR33 공유 문서 head1435db5e…의 MASTER_PLAN/DECISIONS 및 이후 사용자 P2 승인 적용.

## 구현 및 실행 경계

- 기존 CodingAgent.run/메모리/패치/CodingWorkspace/RepositoryTestRunner/LocalCodingModel을 재사용한다. common 승인·상태·취소·예산 ledger를 모델 호출과 모든 작업 경계에서 검사한다. 테스트 파일은 읽기 전용이고 solution.py만 격리 candidate에 수정한다. 원래 Git 저장소에는 적용하지 않는다.
- 모델 결정 한 회를 common step 한 개로 계수한다. 같은 결정에 따른 도구 작업은 전후 승인/시간/범위를 재검사한다. 별도 무제한 도구 루프가 없다. 예약과 backend 실측 토큰은 기존 두 ledger에서 각각 보존하며 Codex 토큰은 null이다.
- public 테스트 통과는 candidate 상태일 뿐이다. 독립 hidden grader 성공 및 현재 승인·제어·전체 deadline을 확인해야 common succeeded가 된다. 실패·중단·알 수 없는 결과와 원문은 private checkpoint/grader artifact에 남긴다.
- CLI는 새 private 임시 루트에 합성 Git/SQLite/세션만 만든다. 운영 DB 경로/사용자 repo 입력 옵션이 없다. 실제 모델 전에 기존 Docker 이미지/소켓을 읽기 확인하며 실패하면 model_calls0으로 중단한다. Docker pull·sudo·host runner는 없다.
- 고정 Windows loopback transport는 WSL 선택 옵션이다. 상수 PowerShell 프로그램에 JSON을 base64 stdin 데이터로 전달한다. localhost11434의 metadata와 선택된 설치 모델의 bounded chat만 허용한다. 프로필·redirect·proxy·기본 자격증명·pull/create/delete/generate/unload·모델 명령 실행을 허용하지 않는다. Codex UI나 전역 설정은 변경하지 않는다.
- 설치 모델 digest와 benchmark digest를 계약 criterion에 결합한다. 모든 신규 adapter/lab/transport 및 기존 coding_agent 소스 해시가 승인 환경에 포함되어 변경 시 재승인을 요구한다.

## 실제 검증

- 관련 격리 테스트 222개 통과(초기 디렉터리 권한 충돌은 새 경로를 처음부터0700으로 생성해 해결; 보안 검사를 완화하지 않음). 최종 전체 QA457시나리오/884실행 통과(162.74s), role_ai/qa/tests 해시 일치. CI 수신은 PR 본문과 PROGRESS에 기록한다.
- Windows Ollama0.40.11 metadata 요청은 실제 성공. 선택 설치 모델 SHA256 d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04, completion/vision/tools/thinking capability, remote 없음, resident0. 실제 생성은 수행하지 않았다.
- 로컬 docker image 조회는 permission denied, sudo 비밀번호 필요. lab의 형식 검사용 placeholder image로 사전검사 실패 경로를 실행해 exit2/model_calls0을 확인했다. 설치 이미지나 모델 전체 평가를 성공했다고 주장하지 않는다.
- GitHub 기존 code-execution job에 scripted model + 실제 public/hidden Docker end-to-end 검사를 추가한다. scripted 토큰 수/성공률은 실제 LLM 성능 지표가 아니다.

## 10 Pass 자체 재검토

| 관점 | 새 발견/수정 | 잔여 위험 |
|---|---|---|
|1 요구 추적|legacy Agent 재사용·common gate 연결|실모델 품질은 후속 실측|
|2 모순|public completed와 common succeeded 분리|개별 consumer도 상태 구분 필요|
|3 기술 사실|WSL loopback 실패/Windows localhost 성공|PowerShell metadata 실측·생성은 미검증|
|4 실행 가능성|Docker 소켓 권한 사전검사/model0 차단|개발 런타임 접근 요청 별도 기록|
|5 구조|새 worker/HTTP 권한 엔드포인트 없이 adapter|trusted Python callback 경계 유지 필요|
|6 보안/복구|취소·철회·정확 경로·hidden 분리 테스트|이미 전송된 모델 입력 회수 불가|
|7 자원|공통 steps/token reservation/deadline/GPU admission|물리 VRAM hard cap/peak 미측정|
|8 예상 결과|진짜 독립 점수만 최종 성공 처리|test_double는 실성능과 구분|
|9 반론|예산 소진·공개 점수 위조·테스트 수정 차단|단일 모듈 합성 과제의 일반화 한계|
|10 재통합|P1 digest/기존 API 유지·새 파일 해시 바인딩|운영 활성화/배포는 미승인|

독립된 10개 AI 검토나 실모델 성능 검증을 의미하지 않는다.
