# Phase 1~2 최종 성능 평가 보고서 — 로컬 위임 개선 미입증

2026-10-31 KST. 공개 자료만 받은 독립 Codex A10과제 생성·독립 Docker 채점과 기존 로컬 위임 B10과제 비교를 완료했다. **A10/10성공, B4/10성공, 비교기 판정 regression/improvement=false.** Codex 전체 비용 구간의 토큰 절감률은 미측정이다. 평가 시스템은 이 부정적 결과를 그대로 기록한다.

P1-03~08/P2-A~D는 단계별 CI·dev 병합 완료다. P2-E #47의 최종 CI/리뷰/병합 여부·SHA는 PR 본문과 Git 밖 `PHASE2_COMPLETION.md`, `phase-1-2-codex-state.json`의 실제 수신으로 확인한다. 이 문서를 작성한 시점에는 최종 문서 커밋 검증 전이며 병합 완료를 미리 선언하지 않는다.

## 단계별 병합 수신

기존 P1-01 #34(cda418736d4c031a970af96d148f436ad0462326), 로그 최적화 #35(2e240cdf11c54a854d77c634003f5310a00c81bb), P1-02 #36(f542610ca9aeeeee345935e07710271decbf048e)를 재사용했다. 아래 새 단계는 각 head에서 필수 CI6개 통과, 충돌/리뷰/미해결 스레드 확인 후 dev에 병합했다.

|단계|PR|dev merge SHA|로컬 테스트 실행 수|검증 범위|
|---|---|---|---|---|
|P1-03|#37|`89f61c4502e702b7dcf527d665ade198909f41c5`|713|합성/회귀 검증·dev 병합|
|P1-04|#38|`67014f7a661f8d14e4ac03183bd2b6ff72a2b3c8`|733|합성/회귀 검증·dev 병합|
|P1-05|#39|`d4dbc2d9ca2ebe4382c61bf63212e2aed961509b`|767|합성/회귀 검증·dev 병합|
|P1-06|#40|`dc7fde777b24cc4fc147a4158861bc9b1dcefc4b`|791|합성/회귀 검증·dev 병합|
|P1-07|#41|`06900f1e52ca671896784c17ef856ac8c6a7f097`|813|합성/회귀 검증·dev 병합|
|P1-08|#42|`cb73dd2230c2c0fc451bb5fe0cbed8c965913431`|830|합성/회귀 검증·dev 병합|
|P2-A|#43|`28629cd70aaf7672a227b7b9d4bf2877da80c9d0`|838|합성/회귀 검증·dev 병합|
|P2-B|#44|`bba1ba1df1a1c7aead21afc1a354978494ad8916`|864|합성/회귀 검증·dev 병합|
|P2-C|#45|`1e340ab5cdea7c37c9fef90a6fc8344f14114428`|884|합성/회귀 검증·dev 병합|
|P2-D|#46|`6ec1c88707589d1bcad6f0fd7a7cb1d7437cc22e`|900|합성/회귀 검증·dev 병합|

현재 최신 병합 dev: `6ec1c88707589d1bcad6f0fd7a7cb1d7437cc22e` (P2-D #46). P2-E는 별도 feature/p2-e-comparison-report이며 완료/CI/PR 수신은 PR 본문과 외부 재개 기록에 기록한다.

## A/B 결과와 범위

|항목|A: Codex 직접|B: 로컬 LLM 위임|
|---|---|---|
|고정10과제 전체 성공|10/10(100%)|4/10(40%)|
|최종 독립 채점|10건,60/60사례 통과|7건,39/42사례 통과|
|미채점|0건|3건,계획60사례 중18미채점|
|최종 상태|성공10|성공4/실패3/예산 제한2/모델 전 승인 차단1|
|실제 생성 근거|별도 fresh CLI10프로세스,도구 실행0|설치된 Ollama 모델9과제 시작,56호출|
|시간 구간|생성86.083067s,유효 재채점28.717817s|supervisor362.933235s|
|환경 오류 진단 시간|최초 채점26.642454s 추가,원문 보존|승인 시계 역행 차단도 전체 시도 포함|
|Codex 사용량|아래 생성 구간별 실제 category 수치|정의·수정·검토 구간 미측정|
|로컬 모델 토큰|해당 없음|9건 측정 소계40,590;차단1건 null|
|GPU|A 장치 지표 미측정,추론은 hosted Codex|장치 전체 관측 peak5,447MiB|

B의 39/42는 채점된 부분 점수다. 이를 전체60사례 통과율이나 전체 성공률92.9%로 표현하지 않는다. 공통으로 최종 채점된7과제에서는 A42/42, B39/42다. scope-read의 실행 단계 제한, duration-parser의 실패/재시도 제한을 모두 토큰 고갈로 해석하지 않는다.

|과제|A 유효 재채점|B 최종 상태|B 공개/숨김 통과|
|---|---|---|---|
|scope-read|6/6|budget_exhausted|미채점|
|memory-revision|6/6|approval_denied_before_model|미채점|
|conversation-bound|6/6|succeeded|2/2 · 4/4|
|budget-check|6/6|failed|2/2 · 3/4|
|safe-log-fold|6/6|succeeded|2/2 · 4/4|
|stable-dedup|6/6|succeeded|2/2 · 4/4|
|merge-intervals|6/6|failed|2/2 · 3/4|
|duration-parser|6/6|budget_exhausted|미채점|
|stable-topology|6/6|succeeded|2/2 · 4/4|
|pagination|6/6|failed|2/2 · 3/4|

## 재현 조건과 공정성 한계

- 구현 digest `f028120546f589a0741cccbbb06bc4a0afd6d760c9be82721a321e99dc334061`, seed7, 동일 공개 task digest와 독립 grader, 동일 Rootless socket/host 및 Python3.12-slim 이미지 `sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`를 사용했다. 실제 B source7b6dfb5와 후속 QA/문서 변경의 core source digest는 동일하다.
- A는 사용자가 명시 승인한 별도 `codex exec` 0.164.0의 새 세션10개다. 기존 ChatGPT 인증을 강제하고 API key 환경변수는 전달하지 않았다. 사용자 config는 이 실행에서 로드하지 않고, 명령/앱/웹/브라우저/이미지/하위 agent 도구를 비활성화했다. readonly sandbox, 명시 payload의 공개 GOAL/stub/test만 전달, 이전 대화/저장소/정답/숨김 테스트 전달0, workspace 변경0을 검토했다. 전체 JSONL에 모델 도구 실행 item은 없었다. 일반 CLI built-in 지침까지 OS 수준으로 제거했다고 주장하지 않는다.
- A는 한 번의 구조화된 답안 생성 후 제출했다. B는 기존 제한된 Coding Agent의 공개 테스트 피드백 루프를 사용했다. A에 숨김 채점 결과를 보내거나 모델을 재호출하지 않았다. CLI provider 기본 모델을 바꾸지 않았으며 JSONL에 정확한 모델 ID가 없어 미확인이다.
- B는 기존 qwen3.5:4b-q4_K_M digest `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`, context4096/output1024/temperature0/thinking off, common steps8/retries0/time300s/token32768, GPU budget6144MiB/reserve1024MiB다. A/B provider context/output/reasoning 예산의 동등성은 입증하지 못했다. task seed가 모델 난수 seed를 고정한다는 뜻도 아니다.
- B를 먼저 수집하고 A를 나중에 평가한 기술 비교다. 실행 순서 무작위화·GPU 독점·동일 provider 하드웨어·반복 실험을 보장하지 않는다. 단일 모듈 합성10과제를 앱 전체 자율 개발 성능으로 일반화하지 않는다.

## 실제 Codex 사용량과 시간

A의 `turn.completed.usage`10개를 원문과 대조한 생성 구간 합계:

|제공된 category|실측 값|
|---|---|
|input_tokens|130,510|
|cached_input_tokens|24,064|
|cache_write_input_tokens|0|
|output_tokens|2,261|
|reasoning_output_tokens|390|

캐시/추론 category를 input/output에 다시 더하거나 뺀 총합을 만들지 않는다. 문자열 길이·예산 추정이 아니다. A의 원문이 있어도 B의 Codex 작업 정의·개입·최종 검토 비용이 없으므로 전체 비용 구간 `codex_tokens`와 절감률은 null로 유지한다. 이 보고서 작성 세션의 토큰을 임의 배분하지 않는다. A의 hosted Codex 토큰과 B의 Ollama40,590을 나누어 Codex 절감률로 표현하지 않는다.

A 시간은 생성86.083067+유효 채점28.717817=114.800884s이며, 초기 환경 오류26.642454s까지 관측한 작업 소계는141.443338s다. B supervisor362.933235s와 시간 구간 차이/초기 오류를 함께 표시한다. 두 방식 모두 사람/Codex의 사전 정의·최종 검토 시간을 완전히 포함한 end-to-end 비용은 아니다. B runtime metric의 시작점은 fixture 준비 뒤이므로 서로 다른 시간 필드를 섞어 합산하지 않는다. 시간은 monotonic 관측이다.

## 초기 오류와 복구 증거

1. A 최초10채점은 supervisor의 umask077이 worker에 상속되어 `/work/runner.py` 접근이 거부됐다. 컨테이너는 시작됐지만 runner/후보는 실행되지 않았고 모두 interrupted였다. 이10건/26.642454s 및 raw log를 보존했다. B worker와 같은 per-process umask022로 **동일 hash 답안만** 재채점해60/60을 확인했다. controller/비공개 부모0700·로그0600, 원본 코드/승인/격리 정책은 유지했다. 모델 재호출/답안 수정은0이다. 이 교정은 사전 등록된 무결한 첫 실험으로 표현하지 않는다. 기존 runner의 엄격한 umask 호환성은 후속 개선 항목이다.
2. CLI의 under-development feature banner가 error item으로 출력돼 최초 supervisor가 중단했다. 원문을 확인해 설정 경고로 분류했고 동일 첫 생성 결과를 검토·인정했다. 명령/앱 등 도구 실행은 없었다. 첫 답안을 다시 생성하지 않았고 남은9과제만 이어갔다.
3. Rootless readiness와 B 시작에서 now가 approval.created_at보다 약0.339초 과거인 wall clock 역행을 관측했다. 승인 검사는 fail-closed했고 시계/만료/권한 조건을 완화하지 않았다. B 모델 전 차단1건은 queued checkpoint와 runtime intent 부재를 근거로 trusted supervisor가 blocked 기록을 별도로 만들었다. core 상태/API 결과를 수정하지 않았다. 짧은 후속 관측에서 재발하지 않은 것으로 원인 해결을 선언하지 않는다.
4. 실제 rootless kernel memory128MiB/swap0/CPU0.5/pids32/nonroot/readonly/networknone/NoNewPrivs1/CapEff0, timeout/OOM/cleanup을 검증했다. QA synthetic fixture의 endpoint 덮어쓰기만 명시 전달로 수정해 실제 위임/독립 채점 통과했다. 운영 daemon/context/권한·지속 서비스·DB를 변경하지 않았다.

## 판정·검증·병합 범위

기존 비교기를 수정하지 않고 actual A10/B10 기록을 입력했다. `paired_coverage_complete=true`, `quality_regression=true`, `conclusion=regression`, `improvement=false`, `codex_token_reduction=null`을 확인했다. CLI exit2는 이 부정적 판정의 명시 결과다. 일치하는 metadata가 공정성·provider 인증을 보장한다는 뜻은 아니다.

평가 시스템과 사실 보고의 완료 조건은 실제 비교/실패 보존·기존 기능 회귀·보안/CI/리뷰 확인이다. 사용자의 지시대로 낮은 B 품질을 성능 개선으로 판단하지 않는다. P2-E PR은 평가 도구와 부정적 실측 결과를 기록하며 기존 VS Code 방식/운영 모델 라우팅을 바꾸지 않는다. 기존 기능의 코드 회귀와 평가 대상 B의 상대적 품질 하락을 구분한다. 운영 위임 활성화·Phase3 개발·모델 교체는 별도 범위다.

기존 source 전체 QA486시나리오/952실행과 PR head90c6bc3의 CI6/6 success, 실제 Docker 실패/복구/독립10과제60사례/위임·unverified 제외를 확인했다. Starlette/httpx testclient deprecation 경고1건은 보존하고 의존성을 바꾸지 않았다. 이번 후속 변경은 문서/실측 기록뿐이다. 최종 문서 head에서도 CI6개·충돌/리뷰·파일 범위를 확인한 뒤에만 dev 병합한다. main/운영 DB·Migration/Render·Cloudflare·Compose·서버·모델 설치/교체/학습/유료 API는 변경하지 않는다.

## 원문 보관과 재개/롤백

Git 밖 private 경로:

- `P2E_AB_RESULTS.json`: A/B20기록·비교·시도/usage/time·프로토콜 검토.
- `p2e-a-only-tpjpbd2o/`: A 원문 JSONL/공개 후보/첫 실패와 교정 채점/CONTEXT_REVIEW/HARDWARE_CONDITIONS_REVIEW. `P2E_A_RUN_CURRENT.json` 포인터.
- `p2e-b-only-dm_99yec/PROTOCOL.json` 및 `P2E_B_ONLY_RESULTS.json`: B 원래 조건·모든 실패/차단·소계. 원래 B-only receipt를 덮어쓰지 않는다.
- `P2E_ROOTLESS_RUNTIME.json`, `P2E_ROOTLESS_LIMIT_PROOF.json`, `P2E_ROOTLESS_SAFETY.json`, `P2E_GRADER_GATE_DIAGNOSTIC.jsonl`: runtime identity/자원/초기 실패·후속 검증/clock.

raw prompt/답안/숨김 사례/계정/사용량 원문은 GitHub에 올리지 않는다. 내용 hash는 인증·실행 권한이 아니다. 실험 종료는 이 사용자 daemon만 식별해 중단하며 결과·합성 DB·원본 데이터를 보존한다. 코드 롤백이 필요하면 PR 범위 revert를 별도 검증하고 운영 DB/권한을 되돌리는 작업과 혼동하지 않는다.

## Phase 3 전 남은 작업

- 현재 로컬4B 모델을 Codex 대신 자동 선택할 근거가 부족하다. 기존 개발 방식을 유지하고, 범용 지시/실패 분류/예산 사용의 개선 가설을 새 승인 범위와 새 held-out 평가로 검증한다. 이번 숨김 사례에 맞춘 답안/프롬프트 튜닝은 하지 않는다.
- 공통 비용 구간의 A/B Codex usage, 정확한 A 모델 ID/조건, GPU·시간 범위 및 반복 실험을 사전에 고정한다. 미측정은 그대로 표시한다.
- runner의 umask077 환경 호환성을 고정 소스·새 실제 Docker 회귀 테스트로 보강하는 후속 변경을 검토한다. 이번 frozen source는 수정하지 않는다.
- clock 안정성은 운영자 변경 범위/승인 후 조사한다. fail-closed를 완화하지 않는다. 운영 배포/DB 이행/모델 설치·교체는 이번 병합에 포함되지 않는다.
