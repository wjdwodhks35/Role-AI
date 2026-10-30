# Phase 1~2 실제 진행 및 성능 보고서 — 실측 비교 미완료

이 보고서는 전체 목표의 완료 선언이 아니다. P1-03~08/P2-A~D는 단계별 테스트·CI·dev 병합을 확인했다. P2-E의 비교/공개 기준선/독립 baseline 채점 구현은 최종 로컬 QA479시나리오/922실행이 통과했으며(CI·PR 수신은 PR 본문),, 실제 A/B 데이터가 없으므로 dev 병합과 Phase 2 완료를 보류한다.

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

## 실측과 미측정

|항목|실제 근거|판정 범위|
|---|---|---|
|로컬 LLM 전체 코딩 성공률|완성된 실모델+독립 Docker 평가 0건|미측정·0%로 환산하지 않음|
|Codex 직접 방식 A vs 로컬 위임 B|공정한 동일10과제 paired 실측 없음|비교 불가|
|Codex 실제 사용 토큰/절감률|사용량 원문 미확보|미측정/null|
|설치 모델 구성 요소 검사|qwen3.5:4b-q4_K_M digest d8b0f5e…38c04, 실제 계획 응답 1회|도구 실행/코딩 채점 없음|
|구성 요소 backend 토큰|입력87·출력32, Ollama 응답 메타데이터|119개는 이 작은 요청의 모델 토큰이며 Codex 절감량 아님|
|구성 요소 전체 시간|9.261871s|IPC/metadata/모델/관측 포함, 전체 코딩 과제 시간 아님|
|GPU 구성 요소 관측|18샘플·오류0, device peak5568MiB, 종료 후1787MiB/resident0|장치 전체 관측, 개별 모델 VRAM/절대 peak/물리 hard cap 아님|
|독립 평가기|P2-B 실제 Docker 참조10과제/60 semantic cases·위조/중단 거부 CI|참조 코드의 검사이며 LLM 성공률 아님|
|위임 통합|P2-C/D scripted model + 실제 public/hidden Docker CI 성공|가짜 모델 토큰/성공은 실제 성능 집계 제외|
|기존 기능 회귀|P2-D 전체465시나리오/900실행, 최종 CI6/6 success|해당 고정 소스의 자동 검증; UI/운영 배포 검증 아님|
|실제 실패→복구 성능|합성 상태/예산·복구 테스트만 존재|실모델 복구 성공률 미측정|

구성 요소 원문 수신은 작업 트리 밖 `/home/serveradmin/role-ai-worktrees/docs/P2D_MODEL_COMPONENT_PROBE.json`과 private 임시 artifact에 보존한다. 계획 응답이 유효해도 독립 채점이 없어 common 상태는 interrupted로 남겼다. 이를 completed로 올리지 않았다.

## 비교 구현의 판정 규칙

- frozen 혼합10과제/동일 seed·조건·소스 hash·반복 수를 확인한다. 합성/test_double/작성 주체 미검증 제출물은 실제 집계에서 제외한다.
- unknown Codex/model call/time/recovery는 null로 보존한다. 미채점은 성공·0점으로 바꾸지 않고 전체 시도 성공률과 채점 coverage를 함께 표시한다.
- 품질 저하가 있으면 낮은 토큰 수만으로 개선 판정하지 않는다. 실제 사용량 원문, 공정성/기기 조건 검토 수신과 최종 CI6개 근거가 없으면 개선을 판정하지 않는다. 내용 hash는 권한·제공자 인증이 아니다.
- 공개 기준선 패키지는 목표/빈 solution/공개 테스트만 포함한다. 정답·숨김 사례·이전 Codex 답안은 내보내지 않는다. 후보는 solution.py만 허용하고 독립 offline grader로 채점한다. 이 세션은 oracle을 작성/열람했으므로 현재 세션 답안을 공정한 Codex 직접 기준선으로 쓰지 않는다.

## 아직 필요한 조건

1. 개발용 격리 Docker 실행 환경 접근: 현재 계정은 `/var/run/docker.sock` permission denied, sudo 비밀번호 필요. 그룹/소켓/서비스 정책을 임의 변경하지 않았다.
2. 공개 자료만 받은 별도 Codex 직접 기준선과 접근 범위 검토 수신. 새로운 유료 API/추가 AI agent 실행을 자동으로 시작하지 않는다.
3. 같은 고정 과제/seed/image/예산/기기 조건에서 실제 로컬 위임을 평가하고 실패·중단도 보존한다. 토큰 사용량 원문이 없으면 미측정 보고를 유지한다.

세부 요청·공개 패키지·명령은 REQUESTS.md에 기록한다. main/운영 DB·마이그레이션/Render·Cloudflare·Compose·서버 설정/모델 설치·교체·학습은 수행하지 않는다. P2-E 실측/검증 및 dev 병합을 확인하기 전 Phase 2 완료 또는 Phase 3 착수를 선언하지 않는다.

## 추천안 후속 진행 수신

2026-10-31: 사용자 승인 추천안에 따라 A/B private Unix socket 선택과 교체 시 승인 무효화를 구현했다. 기존 Docker 제한 preflight를 재사용하며 기본 운영 정책/Task Contract digest는 유지한다. 새 소스용 공개 패키지를 준비했고 이전 패키지는 보존했다. Rootless helper 미설치·관리자 준비 필요와 공식 Codex usage 이벤트/현재 원문 미확보를 구분해 ROOTLESS_SETUP_PLAN/CODEX_USAGE_PROTOCOL/REQUESTS에 기록했다. 이 추가 구현은 실제 A/B 자료를 만들거나 기존 미측정 수치를 채우지 않는다. 최종 QA/CI 수신은 PR #47과 외부 재개 기록에 남긴다.
