# R1 — restrictive umask runner 호환성 및 private parent 검토

사용자 “남은 작업 진행” 범위, 시작 dev `8511e00116e55a041c8bd088d491c44e9521a66a`. 별도 `fix/runner-restrictive-umask`. Phase3/운영 변경 없이 확인된 P2-E 호환성 결함을 보강한다.

## 근거와 변경

`CodeExecutor.execute`의 mkdir(mode=0755)는 호출자 umask077에 의해0700이 된다. P2-E 실제 nonroot Docker가 runner.py를 읽지 못한 원인이다. work mode를 명시0755로 확정하고 외부 임시 부모는0700으로 유지한다. 파일0444/bindreadonly/uid65534/networknone/NoNewPrivs/caps/자원/preflight·승인·cleanup은 유지한다. 애플리케이션에서 process umask를 바꾸지 않는다.

단위 회귀는 pytest 부모의 정책에 영향을 주지 않는 별도 신뢰된 fixture 프로세스에서022/077을 각각 검증한다. 제출 코드를 호스트에서 실행하지 않는다. 실제 Docker QA에도 umask077 smoke를 추가하고 해당 QA 프로세스의 기존 umask는 finally에서 복구한다. 기존 QA 행은 보존하고 신규 selector1행(매개변수2실행)을 추가한다.

기존8511e00 소스에서는 신규2검사가 실패했고 수정 후 관련167개가 통과했다. 새 private Rootless socket과 기존캐시이미지에서 실제 umask077 nonroot 테스트·성공/실패/timeout/OOM/읽기전용/비밀차단/networknone/cleanup 검사가17.392s/exit0으로 통과했다. 로컬 로그는 Git 밖 `.codex-local/logs/`에 보관한다. 전체QA/최종CI/병합 SHA는 PROGRESS/PR의 실제 수신을 참조한다.

## 10 Pass 자체 재검토

|Pass|새 발견/조치|잔여 위험|
|---|---|---|
|1 요구|엄격한umask에서 발생한 실제오류를 별도fix로 제한|모델품질 개선은 별도실험|
|2 모순|컨테이너읽기와호스트비공개를 work0755/parent0700으로 분리|정책을 전체umask022로 바꾸는 우회 금지|
|3 사실|Python mkdir모드는실제processumask에영향,022/077별도fixture 재현|POSIX외runner실행은 기존대로미지원|
|4 실행|기존설치runtime/캐시이미지 재사용·실제Rootless통과|CIrootful결과도 확인 필요|
|5 구조|기존CodeExecutor·권한·TaskContract 재사용|core source digest는변경되어새평가manifest필요|
|6 보안|임시부모0700/파일0444/bindreadonly·nonroot 유지|동일hostuid악성프로세스까지격리하지않음|
|7 자원|GPU/모델 호출0,기존CPU/메모리/pids·timeout/OOM 유지|물리장치격리 주장하지않음|
|8 결과|정상·엄격umask/실패/cleanup/기존umask보존 검증|readiness/시계역행은별도문제|
|9 반론|모의mode검사만으로성공판정하지않고실제Docker검증|개인자료나운영환경으로검증하지않음|
|10 통합|QA계획기존행보존·회귀/CI/PR단계검증|기존P2-E 수신을 새코드성능으로 전용금지|

독립10AI 리뷰가 아닌10관점 자체재검토다. 문제 발생 시 이PR 코드범위를revert해 검증하며 운영DB/데이터/권한 복원 작업은 없다. 기존A/Bfrozen source와결과는 보존한다. 공유기획PR33/31/32 등 기존PR은 자동통합하지 않는다.

최종 로컬 전체QA487시나리오/954실행PASS, snapshot1kbumvde의app/tests/QA SHA256이현재소스와일치한다. 새selector1행은022/077두실행을포함한다. 실제Rootless는readonly/nonroot모두통과했다. 별도clock40초관측에서는역행2회·최대차1.119s로재현되어운영변경없이후속요청파일에기록했다. 이fix는clock승인경계를완화하지않는다. 최종CI/PR/병합수신은외부후속상태와PR본문에기록한다.
