# v1.1 기준선·측정 계획

분석일 2026-10-28, GitHub/로컬 dev `d3d790a6a604374e9c2ec26d8425657d19eddd6c`.
v1.1은 기획 문서 버전이며 구현 완료 버전이 아니다. 두 기획서 원본은 docs에 변경 없이 보관한다.

## Coding runtime 검증 결과

- 구현 기준: `00ee82651b6681db665dcd9192ff49e1a890910e`, PR 합성 merge commit
  `2ecd15bb6bcaf796b74835cb2096f56e36009949`.
- [CI run 145](https://github.com/wjdwodhks35/Role-AI/actions/runs/40641575138):
  273개 시나리오·523회 실행 모두 통과, ordered=true, exit_code=0.
- tests/container/code-execution/rlaif-contract 모두 성공. 실제 Docker unittest에서
  실패→checkpoint/pause→Worker 재생성/resume→재수정→통과, 테스트 0개/전부 skip 거부 확인.
- 모델은 scripted fixture다. 실제 설치 LLM의 작업 성공률·4060 Ti VRAM/지연·실브라우저·
  운영 서버 동작은 측정하지 않았다. 성능 향상·운영 배포 완료라고 표시하지 않는다.
- 문서 보완 커밋의 CI는 해당 PR의 최신 검사 결과로 구분한다. 위 수치는 구현 커밋의 검증 근거다.
- DB schema/운영 Compose/Cloudflare/OpenClaw 보안 설정은 유지한다. 모델·데이터 다운로드,
  실제 GPU 학습·모델 활성화·운영 배포는 수행하지 않았다.

## 착수 전 분석 근거 · 역사적 기록

- 초기 분석에서는 HEAD/Git 상태, v1.0/v1.1 차이, CodingWorkspace/TaskSpec/모델/마이그레이션/
  실행기/역할/Worker/평가/학습·데이터 처리 소스와 기존 계획을 읽었다.
- 초기 분석은 문서와 소스 확인만 수행했다. 이후 승인된 runtime 구현·검증은 위 절에 기록한다.
- #27의 역사적 근거: 신규 임시 Git 테스트 45개, Excel 241개 시나리오/442회 실행 통과,
  CI tests/container/code-execution/rlaif-contract 성공.
  https://github.com/wjdwodhks35/Role-AI/actions/runs/40633204009
  이는 이번 재실행 결과나 Strategy Learner 검증이 아니다.
- 당시 RLAIF CI는 사전학습 가중치 없는 임의 소형 CPU 모델 계약 검사다. 사용자 GPU에서
  실제 모델 학습·품질 개선·운영 활성화를 입증하지 않는다.

## 미측정 지표

| 대상 | 상태 | 추후 측정 조건 |
|---|---|---|
| Recorder 지연·용량·누락 | 최소 기록 구현/성능 미측정 | off/on 동일 작업 replay, p50/p95·bytes/task·포화/장애 지연·gap 비율 |
| Coding 성공·복구율 | runtime 구현/실모델 성공률 미측정 | 고정 task suite/base SHA/model/prompt/tools/budget·실제 runner·독립 테스트. 실패/취소/예산 종료 분모 정책 고정 |
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
