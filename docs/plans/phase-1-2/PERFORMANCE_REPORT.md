# Phase 1~2 실제 진행 및 성능 보고서 — B 실측 완료, A 비교 미완료

2026-10-31 KST. P1-03~08/P2-A~D는 단계별 CI·dev 병합을 확인했다. P2-E는 Draft PR #47이며 아직 병합하지 않았다. B 전체 성공은 **4/10(40%)**, A는0건이므로 A/B 우열·토큰 절감을 판정할 수 없다. 낮은 성공률을 감추거나 시험을 완화하지 않는다.

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

## 고정 조건과 실제 B 결과

구현 digest `f028120546f589a0741cccbbb06bc4a0afd6d760c9be82721a321e99dc334061`, 공개 패키지 `p2-e-public-baseline-wl5dpr0y`, seed7, 과제별1시도. 설치된 qwen3.5:4b-q4_K_M digest `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`, context4096/output1024/temperature0/thinking off, common steps8/retries0/time300s/token32768, GPU budget6144MiB/reserve1024MiB를 고정했다. task seed가 모델 난수 seed를 고정한다는 뜻은 아니다.

Python3.12-slim 이미지 digest `aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`, 사용자 전용 Rootless daemon, 합성 DB/합성 과제만 사용했다. 실패 후 모델 교체·숨김 피드백·재시도·예산 확대를 하지 않았다. 사전 smoke는 아래10시도에서 제외한다.

|과제|최종 상태|모델 호출|측정 모델 토큰|독립 공개/숨김 통과|
|---|---|---|---|---|
|scope-read|budget_exhausted|8|5532|미채점|
|memory-revision|approval_denied_before_model|0|미측정|미채점|
|conversation-bound|succeeded|7|5357|2/2 · 4/4|
|budget-check|failed|6|4105|2/2 · 3/4|
|safe-log-fold|succeeded|6|4433|2/2 · 4/4|
|stable-dedup|succeeded|6|3853|2/2 · 4/4|
|merge-intervals|failed|6|4645|2/2 · 3/4|
|duration-parser|budget_exhausted|5|3544|미채점|
|stable-topology|succeeded|6|4723|2/2 · 4/4|
|pagination|failed|6|4398|2/2 · 3/4|

- 모델 시작9건, 최종 독립 채점7건, 미채점3건. 채점된42사례 중39통과이며, 계획60사례 중18은 미채점이다. 이를 전체60사례 통과율이나 성공률92.9%로 표현하지 않는다.
- 실제 모델 호출56회. 측정된9건의 backend 토큰 **소계40,590**; 승인 단계 차단1건은 null이므로 완전 측정 합계로 표현하지 않는다. Codex 토큰·절감률은 null이다.
- 일관된 외부 supervisor 구간 총 **362.933235초**(프로세스/fixture 준비 포함). 개별 runtime metric은 준비 후부터 측정하므로 서로 합쳐 전체 시간을 만들지 않는다. Codex의 작업 정의·최종 검토 시간은 포함하지 않는다.
- 장치 전체 관측 peak **5,447MiB**. 개별 모델 VRAM·물리 hard cap·샘플 사이 절대 peak를 보장하지 않는다. 종료 후 Ollama resident0 확인.
- scope-read는 실행 단계 제한, duration-parser는 실패/재시도 제한에 도달했다. status budget_exhausted를 모두 토큰 고갈로 해석하지 않는다.

## 실패 보존 및 환경 검증

사용자가 uidmap을 설치한 뒤 newuidmap/newgidmap과 기존 subuid/subgid를 확인했다. 운영 daemon·전역 context·사용자 지속 서비스·linger를 바꾸지 않고 private socket/data/exec/config로 사용자 daemon을 실행했다. kernel 실측: memory.max134217728, swap.max0, cpu.max50000/100000, pids.max32, UID/GID65534, NoNewPrivs1/CapEff0, root/work 읽기 전용, loopback만 존재, GPU 장치 미노출. 기존 timeout/OOM/cleanup 검사도 통과했다.

실제 독립 grader 최초 실행은 승인 단계에서 실패했다. 진단 재실행은 참조10과제/60사례 및 위조/중복/timeout 거부를 통과했으나, 별도 재현에서는 created_at보다 now가 약0.339초 과거인 **wall clock 역행**을 관측했다. 권한 검사는 올바르게 fail-closed했다. 서버 시계·만료 조건·승인 검사를 변경하지 않았다. 뒤의 짧은 관측에서 재발하지 않았다고 원인을 해소한 것으로 보고하지 않는다.

정식 B의 memory-revision도 모델 시작 전 approval_denied로 차단됐다. 원문 traceback, queued checkpoint 및 runtime intent 부재를 보존했다. 신뢰된 외부 supervisor가 그 증거로 blocked/model_calls0 기록을 별도 정규화했으며 기존 queued 상태나 API 결과를 수정하지 않았다. 최초 실패 후 같은 과제를 재시도하지 않고 아직 시작하지 않은 나머지 과제만 계속했다. 각 과제의 clock_step_suspected는 관측 플래그이며 모두 동일 원인으로 단정하지 않는다.

QA의 synthetic_environment가 환경 socket을 덮어쓰는 문제를 발견해 `qa/check_task_delegation.py`에서 명시적으로 전달했다. 실제 private endpoint에서 scripted 위임→public Docker→독립 Docker 통과, 직접 참조 baseline도 채점했다. 참조 답안은 Codex 작성 근거가 없어 unverified_submission으로 실제 A에서 제외했다. 기존 sandbox/계약 digest/승인 정책은 변경하지 않았다.

## 검증 수신과 보관

이전 고정 소스 전체 QA486시나리오/952실행 통과(snapshot `_qkvn9fx`), 이전 head ae3d04c의 CI6/6 success. 이번 QA socket 전달 수정은 실제 private Docker 통합으로 검증했다. 새 head의 CI 상태는 PR #47과 외부 재개 기록을 확인한다. 테스트 통과는 실모델 품질40%와 별개의 결과다.

비공개 원문/합성 후보/DB/로그는 Git 밖에 보관한다:

- `/home/serveradmin/role-ai-worktrees/docs/P2E_B_ONLY_RESULTS.json`: 전체10시도/집계/미측정/비교 미완료.
- `/home/serveradmin/role-ai-worktrees/docs/p2e-b-only-dm_99yec/PROTOCOL.json`: 실행 전 고정 조건 및 source identity, 모든 실패 기록.
- `P2E_ROOTLESS_RUNTIME.json`, `P2E_ROOTLESS_LIMIT_PROOF.json`, `P2E_ROOTLESS_SAFETY.json`: 실제 endpoint/제한/초기 실패와 후속 검증.
- `P2E_GRADER_GATE_DIAGNOSTIC.jsonl`, `P2E_CLOCK_OBSERVATION.json`: 합성 승인 실패 및 clock 관측.

내용 hash는 제공자 인증·실행 권한이 아니다. raw prompt/숨김 사례/후보/사용량 원문/계정 정보는 GitHub에 올리지 않는다. rollback은 이 평가 전용 daemon만 중단하고 결과를 보존하는 범위다. 운영 DB migration·서비스·설정·권한·모델 설치/교체는 수행하지 않았다.

## 남은 조건

공개 자료만 받은 독립 Codex A10과제, 실제 접근 범위 검토와 동일 조건의 채점·비교가 필요하다. 현재 세션은 oracle을 열람해 A 답안을 만들지 않는다. 구체 요청은 REQUESTS.md에 기록한다. 추가 독립 에이전트 실행의 명시적 승인을 기다리는 동안 문서·QA·CI 작업을 진행한다.

원래 사용자 요청은 Codex 토큰 **미측정 표시를 허용**한다. 사용량 원문 부재만으로 완료 기준 변경 승인을 요구하지 않는다. 미측정이면 토큰 절감률을 검증했다고 주장하지 않으며, 품질 저하를 개선으로 판정하지 않는다. A0 상태에서는 비교가 미완료이므로 P2-E/Phase 2 완료·dev 병합·Phase 3 착수를 선언하지 않는다.
