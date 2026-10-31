# R2 — Codex usage 증거·비용 구간 10 Pass

시작 dev `2689dc6e8cad3856f00993a22a782323834daf17`(R1 #48 병합 확인 후 시작). 사용자 “남은 작업 진행”의 계측 보강 범위다. 앱 runtime/TaskContract/운영 권한에 연결하지 않는 offline 표준 라이브러리 reader로 한정한다.

공식 CLI JSONL event·usage 문서 확인 후 기존 `.codex-local/logs` 보관 방식을 재사용했다. 문서: [OpenAI non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode#make-output-machine-readable). VSCode 내부 API나 자동 가로채기/일일 계정 토큰의 비례 배분을 가정하지 않는다. 토큰 범주가 전체 비용·실제 모델 ID·성공률을 증명하지 않는 점을 명시했다.

새 unit20개와 기존 wrapper 포함44개(플랫폼전용2제외)가 로컬 통과했다. 실제 기존A10세션 raw SHA/종료코드를 대조해 생성 소계130510input/2261output 및 별도cached/reasoning을 재현했다. 정의·검토가 없어 완전합계null/exit2. 새 모델/API호출0. 실제 receipt는 Git밖 FOLLOWUP_USAGE_EVIDENCE_RESULT.json 및 private root에 보관한다. 최종 Windows/Ubuntu CI·전체 회귀·PR/merge 수신은 PR/후속 상태에 기록한다.

|Pass|새 발견/조치|잔여 위험|
|---|---|---|
|1 요구|생성 구간·전체 비용을 분리하고 누락을null로 유지|향후definition/review실측필요|
|2 모순|범주중복합계·Ollama/Codex토큰비율금지|집계범주를소비자도구분해야함|
|3 사실|공식event와실제CLI0.164.0원문대조|다른버전/schema는재검토필요|
|4 실행|stdlib offline reader/기존raw보관·CLI만추가|새세션수집기·모델실행은별도|
|5 구조|앱/TaskContract/MetricRecord/승인경로변경없음|해시만으로권한/신원인증불가|
|6 보안|원문·명령·키비반사/크기·경로·링크·중복거부|동일uid악성프로세스완전격리아님|
|7 자원|원문4MiB/16384event/session128개·신규추론0|제공자가누락한청구정보는알수없음|
|8 결과|실패/잘림/unknown/optionalnull/정상0/부분합검증|원문에없는단계는복원불가|
|9 반론|정상소계가있어도완전비용·개선은false/null|모델ID·provider/hardware동등성별도검토|
|10 통합|기존Windows/Ubuntu CI자동발견·scope고정|CI확인전병합금지·clock해결미포함|

독립10AI리뷰가아닌자체검토다. 기존 frozen A/B 성능 결과를 바꾸지 않는다. 롤백은 reader/테스트/문서 범위의 revert로 충분하며 DB/모델/시스템 설정은 없다. 모델품질의새인과실험은시계역행및환경원인해결후별도프로토콜로진행한다.
