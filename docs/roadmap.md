# 개발 단계

목표 범위의 “약 50%”는 최종 비전의 범위 구분이며 코드 완성률이 아닙니다.
이번 PR은 Phase 1 기반만 구현합니다.

## Phase 1 — feature/project-bootstrap

인증, SQLite Repository, 한국어 UI, 대화 저장, 설정, Provider/Assistant 계약,
Ollama 기본 경로, Mock 자동 테스트, Docker Compose, CI.
실제 OpenClaw/OpenAI 연결은 범위 밖입니다. 테스트 결과를 PR에 기록합니다.

## 다음 기능 PR

1. feature/openclaw-integration
   - 실제 소유자용 비공개 Gateway 연결, 인증과 상태 probe.
   - 서버에서 생성한 세션 매핑, 제한된 도구 계약, 기본 비서 복귀 설정.
   - 공개 사용자는 공유 운영자 Gateway에 전달하지 않기.
2. feature/openai-provider
   - OpenAI 공식 API와 vLLM의 실제 Provider, 모델 허용 목록.
   - 서버 비밀 관리/암호화 키 보관, 사용자 설정 UI.
   - 호출 전 동의/예산 예약, 사용량 정산, 비용 한도와 실패 정책.
   - 자동 재시도는 중복 비용·도구 실행을 고려해 제한하고 Mock으로 대체하지 않기.
3. feature/permission-gate
   - Tool Registry, 사용자·도구·대상·작업별 정책, 감사 로그.
   - 실행 내용/영향을 표시하는 승인 UI, 요청 내용과 결합된 단회 승인.
   - 파일/shell/이메일/일정 등 효과 발생은 승인 후에만 수행.
4. feature/memory-rag
   - 문서 제한/업로드, 사용자별 벡터 검색, 근거 추적.
   - 검증된 기억 추출, 중복 제거, 수정/삭제/보존 정책.
5. feature/job-queue
   - Worker/영속 작업 상태/취소/재시도/복구.
   - GPU 임계 구역과 모델 순차 실행, Whisper/LLM 충돌 방지.
6. feature/meeting-skill
   - 동의 확인 및 제한된 미디어 업로드, FFmpeg 전처리, faster-whisper.
   - 타임스탬프와 회의 요약/결정/Action Item, 선택적 화자 분리.

## 이후

외부 API 확장, 실시간 회의 녹음, Windows PC 제어 및 EXE 패키징,
격리된 학습 데이터 관리와 Teacher/Reviewer 검수,
승인된 LoRA/QLoRA 학습·평가·롤백·모델 배포.

## 공개 배포 전

HTTPS, 안전한 비밀 관리, 신뢰할 프록시, 분산 Rate Limit,
계정 정책, 데이터 보존/삭제, 백업·복원, 감사 로그, 침투/권한 테스트.
테스트 통과만으로 실서비스 배포를 완료했다고 보고하지 않습니다.
