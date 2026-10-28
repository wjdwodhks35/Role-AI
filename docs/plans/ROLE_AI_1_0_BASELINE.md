# v1.1 기준선·측정 계획

분석일 2026-10-28, GitHub/로컬 dev `d3d790a6a604374e9c2ec26d8425657d19eddd6c`.
v1.1은 기획 문서 버전이며 구현 완료 버전이 아니다. 두 기획서 원본은 기존 체크아웃에 보존한다.

## 확인 근거와 한계

- 계획 작성 시 HEAD/Git 상태, v1.0/v1.1 차이, CodingWorkspace/TaskSpec/모델/마이그레이션/
  실행기/역할/Worker/평가/학습·데이터 처리 소스와 기존 계획을 읽었다.
- 이후 사용자 승인으로 R01 코드·테스트를 별도 feature 브랜치에서 구현한다. DB·설정·Gateway·
  Compose는 유지한다. 합성 이벤트와 임시 Git의 off/on/장애 비교를 검증하며 최종 수치는 PR과 CI에 남긴다.
- #27의 역사적 근거: 신규 임시 Git 테스트 45개, Excel 241개 시나리오/442회 실행 통과,
  CI tests/container/code-execution/rlaif-contract 성공.
  https://github.com/wjdwodhks35/Role-AI/actions/runs/40633204009
  이는 이번 재실행 결과나 Strategy Learner 검증이 아니다.
- 당시 RLAIF CI는 사전학습 가중치 없는 임의 소형 CPU 모델 계약 검사다. 사용자 GPU에서
  실제 모델 학습·품질 개선·운영 활성화를 입증하지 않는다.

## 미측정 지표

| 대상 | 상태 | 추후 측정 조건 |
|---|---|---|
| Recorder 지연·용량·누락 | 미구현/미측정 | off/on 동일 작업 replay, p50/p95·bytes/task·포화/장애 지연·gap 비율 |
| Coding 성공·복구율 | 루프 미구현 | 고정 task suite/base SHA/model/prompt/tools/budget·실제 runner·독립 테스트. 실패/취소/예산 종료 분모 정책 고정 |
| Retriever 효과 | 미구현/미측정 | 검색 off/on, 신규 repo/문제 계열, 동일 모델/예산/seed 정책, 누출 감사 |
| Optimizer 효과 | 미구현/미측정 | 전략 버전·배정/반복·평가 계획 사전 고정, 성공/회귀/p50/p95/자원/불확실성 |
| 전략 경험 파인튜닝 | 미구현/미측정 | 권리·검증·누출 심사한 데이터, 비학습 repo/문제군, 동일 추론 예산·기준 모델 비교 |
| TTFT·VRAM/RAM/CPU | 운영 미측정 | 실제 사용자 호스트·모델/설정 hash, cold/warm·대화/GPU 직렬화·전환 비용 |
| 두 Gateway·Windows 실행 | 실환경 미검증 | 별도 승인된 설정·실행 검사. 이 계획으로 도구 권한을 부여하지 않음 |

표본 수·통과 기준·최소 실익·허용 지연 증가는 평가 전에 고정한다. 임의의 진행률/성공률/
향상률을 쓰지 않는다. 경험 개수만으로 Retriever를 활성화하지 않는다. 작은 표본이나 불명확한
차이는 inconclusive로 판정하고 기본 전략/검색 미사용을 유지한다.
Recorder 누락을 허위 성공으로 채우지 않으며 coding outcome과 recording_status를 분리한다.
평가 시스템 오류도 전략 성공이나 실패 0점으로 바꾸지 않는다.

## R01 로컬 검증

- 신규 Strategy 이벤트 테스트 58회와 기존 Workspace 테스트 45회, 총 103회 통과.
- 실제 임시 Git에서 기록 off/on/sink 장애의 원본·승인·patch 결과 일치를 검증했다.
- schema 원문/비밀 거부, 최소 필드 추출, scope/동의·숨김 평가 제외, 멱등·충돌·용량·busy·
  예외와 기록 불가, immutable snapshot, 부작용 없는 memory/no-op를 합성 입력으로 검증했다.
- Ruff·JavaScript 구문·기존 인증/IME Node 검사 통과. TestClient 의존성 deprecation 경고 1건.
- schema/마이그레이션/실제 모델·GPU/운영 서비스 변경은 없다. 성능 향상·영속성·재시작 복구는
  검증하지 않았으며 완료라고 표시하지 않는다. 전체 회귀 결과는 PR의 CI 근거로 보고한다.
