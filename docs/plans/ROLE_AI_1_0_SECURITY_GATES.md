# v1.1 보안·승인·데이터 경계

## 보존과 승인

계획 검토 후 사용자의 진행 승인으로 R01 계약·memory/no-op 기록·테스트를 구현한다.
DB/설정·서비스 변경이나 배포는 포함하지 않는다. feature 브랜치에서 dev 대상 PR로 제출한다.
구현, PR 병합, 운영 배포, 모델/데이터 다운로드, 실제 GPU 학습, 모델 활성화, 권한 변경은
각각 승인 범위를 확인한다. 최신 사용자 제한이 과거 상시 승인 문구보다 우선한다.

- 기존 인증/세션/CSRF/tenant 경계를 유지하며 spec의 user_id 문자열만 믿지 않는다.
- SQLite v3의 사용자/대화/기억/승인/학습 데이터·백업 경로를 보존한다.
- 대화 OpenClaw는 owner/loopback/token/로컬 모델/빈 fallback·tools.deny=["*"] 유지.
  Windows 제어 Gateway와 코딩 runner를 분리한다.
- Compose/볼륨/환경변수/서비스와 다른 프로젝트는 변경하지 않는다.
- 무료 외부 모델도 자동 활성화하지 않는다. 기능 opt-in과 외부 데이터 전송 동의를 따로 검사한다.

## Recorder 장애와 권한 검사

Recorder는 선택적 telemetry이며 코딩 실행 승인·필수 보안 감사의 대체물이 아니다.
DB lock/timeout/full/schema 불일치 등은 별도 트랜잭션·유한 대기/큐에서 종료하며 코딩 결과를
바꾸지 않는다. recording_status=unavailable/gap을 표시하고 성공 저장·완전한 이력을 주장하지 않는다.
저장 확인이 없는 경험은 검색/최적화/학습에 쓰지 않는다.
반면 동의/소유권/실행 승인 불명은 해당 접근을 거부한다. 보안 승인은 fail-closed이며 Recorder
fail-open을 이유로 승인이나 필수 감사를 우회하지 않는다.

## 데이터 최소화·보존

- 초기에는 서버 ID/enum/hash/계수/단조 시계 경과시간/제한된 artifact ID만 저장한다.
- .env/토큰/개인 대화·사내 문서/전체 소스·diff/도구 로그·args·절대 경로는 이벤트에 넣지 않는다.
  문제 요약은 기본 null이며 추가 동의와 정제 검토 후 제한적으로만 허용한다.
- 정규식만 믿지 않고 허용 필드/enum/형식/길이를 먼저 검사한다. 합성 비밀·PII·주입 canary로
  DB/오류/인덱스/export까지 검사한다. 검열을 위한 추가 LLM/외부 호출은 없다.
- artifact는 서버가 소유권/타입/보존을 검증한 불투명 참조 ID다. 경로·URL을 실행/fetch하지 않는다.
  원본 로그가 필요하면 별도 격리 저장·동의·보존 기간·quota를 승인받는다.
- 사용자/프로젝트 간 자동 공유·검색은 없다. 삭제/철회는 검색 인덱스·캐시·미사용 학습 후보도
  무효화한다. 기존 백업은 자동 덮어쓰지 않으며 복원 시 삭제/보존 정책을 재적용한다.

R01은 이를 위한 영속 DB/삭제/동의 관리 API를 제공하지 않는다. 인증된 서버 actor가 기존
작업 승인과 별도로 최소 메타데이터 동의를 확인한 뒤 내부 recorder를 호출해야 한다.
기본 disabled/no-op이며 hidden_evaluation은 기록하지 않는다. ID hash는 재식별 가능한
가명 참조이지 익명화나 권한 증명이 아니다. Memory snapshot은 내부 디버깅 API이며 HTTP로
노출하지 않는다. 임의 외부 sink·DB sink는 받지 않고 내장 bounded local sink만 사용한다.

## 검증·검색·학습

실행 관찰, 재사용 검증, 일반화된 전략 우세, 학습 적격은 별도 상태다. test_result=passed라도
독립 평가 not_run·candidate_only를 우수 전략으로 자동 승격하지 않는다. 실패 사례도 근거
검증 후 검색할 수 있지만 실패 응답을 자동 정답 completion으로 학습시키지 않는다.
숨김 최종셋의 문제/정답/해설/파생 요약은 일반 경험·검색·학습 export에서 제외한다.
평가 actor/저장소를 분리하고 개선 에이전트에는 승인된 집계 판정만 제공한다.
개발/검증/최종셋은 repo/문제 계열로 나누고 교차 중복을 확인한다.
기존 exact-match나 모델 judge만으로 Coding Evaluation Agent 완료라고 표시하지 않는다.
Retriever는 근거 제안만 하고 전략 안의 명령을 실행 승인으로 해석하지 않는다.
Retriever/Optimizer는 즉시 비활성화·이전 버전 복귀가 가능해야 한다.
데이터 심사/export, GPU 학습, 운영 모델 활성화는 별도 승인이다.

## DB 적용·롤백

기존 기억/대화/학습 테이블을 전략 저장소로 재해석하지 않는다. 신규 테이블/인덱스를 additive
migration으로 제안하고 당시 최신 버전 번호를 확인한다(현재 v3).
initialize_database는 구 schema/새 migration 기동을 엄격히 검사하므로 flag-off만으로
구 DB 기동·구버전 복귀가 가능하다고 가정하지 않는다. v3 fixture·외래키·오프라인 백업/
새 경로 복원·구버전 호환을 먼저 검증한다. 운영 변경은 별도 승인 후 중지/백업/적용/복원
경로를 확인하며 자동 drop/downgrade하지 않는다.
