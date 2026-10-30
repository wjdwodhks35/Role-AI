# P2-D 성능 계측 및 미측정 구분

시작 dev `1e340ab5cdea7c37c9fef90a6fc8344f14114428` (#45). MASTER_PLAN P2 및 DEC012~014/016 유지. 공유 PR33 unchanged draft.

- strict/frozen MetricRecord: backend 실측/보수 예약/Codex null, task·seed·조건·구현 hash, 상태·회복·시간·장치 VRAM 샘플을 구분한다. budget에서 실제 토큰을 역산하지 않는다. Codex 수치는 사용량 원문 receipt hash 없이는 허용하지 않는다(자동 API 측정/암호학적 인증을 의미하지 않음).
- 실패/차단/중단도 전체 시도 성공률 분모에 포함한다. 미채점은 0점으로 만들지 않고 scored test rate + unscored coverage로 함께 표시한다. 동일 run 중복 및 합성/test_double의 실제 성능 집계를 차단한다. 회복률은 기존 interrupted_not_replayed 이벤트가 있는 실행 단위로 계수하고 분모0이면 null이다.
- GPU observer는 nvidia-smi의 bounded read-only 측정을 사용한다. 누락/오류/샘플 상한/미종료를 표시하고 null을0으로 대체하지 않는다. 장치 전체 observed peak이며 개별 모델 VRAM·절대 peak·물리 hard cap이 아니다. 현재 권한·GPU admission을 변경하지 않는다.
- CLI는 원본 raw 결과와 GPU samples/metrics를 private artifact에 보존한다. Docker 사전검사 차단도 blocked-metrics로 남긴다. 실행 시간은 fixture 준비 후 metadata/승인·모델·채점·관측까지의 wall time이다. 동일 조건 비교는 별도 P2-E에서 검증한다.
- 이전 delegation receipt가 있는 계약의 재실행은 새 계약을 요구한다. 기존 보고서를 덮어쓰던 가능성을 receipt lock + 존재 검사로 차단한다. 자동 replay/삭제나 기존 P1 상태 전이 완화는 없다. benchmark 일회 실행의 재시도는 새로운 계약/run이고 기존 artifact는 보존된다.

## 검증 근거

관련 단위48개 통과. 전체 QA465시나리오/900실행 통과, app/tests SHA256 일치. 실제 Docker용 QA checker 측정 연결은 최종 CI에서 추가 검증한다. CI 수신은 PROGRESS/PR 본문에 기록. CI의 실제 Docker pipeline 결과를 MetricRecord로 바꾸고 scripted 결과가 실제 집계에 들어가지 않는지도 확인한다.
실제 GPU observer 단독 probe: 샘플3, 오류0, observed device peak1787MiB, model_calls0. 이 값은 유휴/장치 관측이며 모델 사용량·성능 수치가 아니다. 비공개 수신은 작업 트리 밖 docs/P2D_GPU_OBSERVER_PROBE.json. 실제 LLM/Codex 성공률·토큰 절감은 미측정, Docker 접근 요청은 계속 유효하다.

## 10 Pass 자체 검토

|관점|새 발견/수정|잔여 위험|
|---|---|---|
|1 요구|토큰/시간/GPU/반복/실패·복구 항목 연결|실제 비교 데이터 필요|
|2 모순|reservation·문자 수와 실측 분리|Codex 자동 측정 없음|
|3 사실|GPU 실제3샘플 관측|모델 실행과 혼동 금지|
|4 실행|권한 부족은 모델0+차단 기록|Docker 접근 별도 요청|
|5 구조|기존 ledger/grader 재사용·strict schema|trusted caller receipt 경계|
|6 보존|receipt lock으로 이전 실행 덮어쓰기 차단|별도 새 계약으로 재시도 필요|
|7 자원|샘플/시간/오류 상한 및 기기 total 변경 감지|샘플 간 peak 누락 가능·장치 공유 경쟁|
|8 결과|합성 제외/실패 분모/미채점 coverage|소규모 표본 일반화 불가|
|9 반론|중복/거짓 success/없는 usage 증거 거부|제공된 receipt 자체의 독립 감사 필요|
|10 통합|P1 schema/digest/API/운영 정책 유지|P2-E 실측 비교 미완료|

독립 AI 검토/실모델 성능 검증을 뜻하지 않는다.
