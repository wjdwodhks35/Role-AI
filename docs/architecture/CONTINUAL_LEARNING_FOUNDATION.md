# 지속 학습 기반: Phase 0/1 구현 및 검증 계획

## 범위

지속 학습 설계의 첫 단계로 운영 기준선의 재현 형식과 논리 에이전트 구성 계약을 추가한다. 기존 운영 데이터와 미커밋 기능을 새 브랜치에 일괄 반입하지 않는다.

1. Phase 0: 명시적으로 지정한 소스 파일 해시, Git commit/dirty 여부, 비밀값을 제외한 설정의 해시를 수집하는 읽기 전용 기준선 도구. 실제 LLM 평가와 소스 기준선은 별개 상태로 기록한다.
2. Phase 1: Conversation/Coding/Research/Knowledge/Learning/Evaluation 역할 정의, 엄격한 JSON schema/loader, 역할별 제한·모델 참조·prompt 버전·로그 namespace, 로컬 소유자 전용 읽기 조회 API.
3. 기존 Settings와 인증/세션·CSRF·DB v3를 유지한다. 설정 파일이 없도록 선택한 기본값에서는 내장 역할 정의만 사용한다. 파일을 명시했는데 없거나 잘못되면 조용히 기본값으로 돌아가지 않는다.
4. 역할은 `definition_only`다. 이 PR은 추론 큐/자동 실행/도구 접근·외부 전송/학습/배포를 연결하지 않는다. 기존 대화 프롬프트·모델 선택·응답 형식을 바꾸지 않는다.

## 검증 계획

- 여섯 역할·버전·결정적 config hash, 역할별 허용 capability·timeout/max steps/모델 참조 검사.
- 잘못된 key, 역할 중복/누락, 권한 확대, 비밀 필드, 경로 탈출·symlink·대형 파일·없는 명시 파일 거부; 잘못된 설정의 원문이 오류에 노출되지 않음.
- 조회 API의 미인증/일반 사용자/public mode 거부, 소유자 조회, 수정 메서드 거부, no-store 헤더.
- 조회로 DB schema/파일·모델 호출/실행 권한이 변경되지 않음. 기존 대화·인증·OpenClaw 회귀 검사.
- 기준선 결과에 secret/DB 내용/파일 원문을 포함하지 않으며 명시한 소스 이외의 파일을 읽지 않음. 측정하지 않은 LLM 성능은 `not_measured`.
- GitHub 기존 CI와 신규 기반 테스트를 통과한 PR만 dev에 병합. 운영 배포·모델 변경·실제 학습은 별도 승인.

## 이후 순서

Phase 2 소스별 읽기 전용 수집/Raw·manifest·전처리/격리 → Phase 3 개인화·검색 → Phase 4 중앙 큐와 역할별 실행 → Phase 5 승인된 개선 실험 → Phase 6 운영 UI.

Phase 0의 전체 실측 완료나 Phase 1의 전문 에이전트 실행 완료를 이 기반 코드의 완료와 혼동하지 않는다. 공개 진단셋과 숨김 최종 평가셋은 분리하고, 기존 QA·모의 결과·실제 LLM 결과를 별도로 보고한다.

## 구현 사용법

- `GET /api/agents`: 기존 세션 인증을 거친 로컬 소유자만 조회 가능. 여섯 역할의 정의, 현행 로컬 모델 참조, 버전별 프롬프트 해시, timeout/max steps, 로그 namespace, config hash를 제공한다. `execution_enabled=false`, `scheduler_enforced=false`는 고정이며 작업 실행/로그 기록이 연결되었다는 뜻이 아니다.
- 기본은 내장 정의이며 파일 접근도 필요 없다. 사용자 설정은 `ROLE_AI_AGENT_CONFIG_DIR`과 `ROLE_AI_AGENT_CONFIG_FILE`로 **명시했을 때만** 읽는다. JSON은 기존 의존성과 표준 라이브러리를 재사용하기 위한 선택이며 제안 YAML을 OpenClaw 설정으로 직접 전달하지 않는다.
- [설정 예시](../../config/agents/example.json)는 자동 적용되지 않는다. 알려지지 않은 필드, 비밀 필드, 임의 model ID, 역할별 capability 확대, 실행 모드 전환은 거부한다. 명시된 파일 누락/손상 시 기본값으로 조용히 대체하지 않는다. 설정 변경에는 기존 운영 승인 절차가 적용된다.
- `coding_local`은 기존 coding 설정이 있는 경우만 그것을 참조하고, 그렇지 않으면 현행 local model을 참조한다. 전용 코딩 모델이 새로 설치·선정되었다는 의미가 아니다. 새 역할 지침은 `agent_prompts.py`에 있으나 기존 대화 프롬프트에 주입하지 않는다.
- 기준선 도구는 `python -m role_ai.baseline_manifest --checkout <체크아웃> --output <새 로컬 JSON 파일>`로 사용한다. 모델/DB/Docker/Gateway를 호출하지 않고 정해진 소스 파일의 해시와 Git 식별 정보를 수집한다. `.env`와 토큰·파일 원문을 출력하지 않는다. 환경변수는 Settings에서 읽되 `.env` 파일은 읽지 않는다. 비밀이 아닌 모델/기능 상태 일부의 해시만 기록하므로 전체 운영 설정 해시가 아니다.
- 기준선 결과는 기본적으로 로컬 보관한다. 기존 출력 파일을 덮어쓰지 않으며 파일 권한은 0600이다. `performance.state=not_measured`와 scope를 확인해 운영 이미지/실제 모델 성능의 기준선으로 오인하지 않는다.

## 로컬 검증 결과

- 신규 역할 구성/기준선 테스트 및 기존 API·보안 경계·OpenClaw·마이그레이션 회귀 **83개 통과**. 임시 DB·합성 데이터·모의 제공자를 사용했다.
- 구성 조회의 미인증/비소유자/public 모드 차단, 수정 메서드 차단, 모델 호출 없음·DB 스키마 유지 확인. 일반 채팅의 제공자 호출은 기존처럼 1회이며 registry dispatch가 추가되지 않았다.
- Ruff·compileall·JavaScript 구문·기존 인증/한글 입력 Node 검사 통과. 실제 CLI로 새 로컬 기준선 JSON 저장 확인. 새 모델 호출·가중치 학습·운영 변경은 수행하지 않았다.
- 의존성의 TestClient deprecation 경고 1건. 전체 서비스의 성능 향상·실제 OpenClaw 동작이나 Phase 0 실측 완료를 주장하는 검증이 아니다.

기존 Excel QA 계획의 210개 시나리오는 보존하고 신규 구성·기준선 18개 시나리오를 추가했습니다. `qa/update_foundation_plan.py`는 중복 추가 없이 계획과 요구사항 커버리지를 갱신하며, 기존 strict QA 검사를 유지합니다.
