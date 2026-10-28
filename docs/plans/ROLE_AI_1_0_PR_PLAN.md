# Role-AI 1.0 v1.1 기능별 PR 계획

사용자의 승인으로 Coding Agent runtime을 구현했으며 현재 범위는 Coding Agent 실동작뿐이다.
당시 최신 dev → 독립 feature/fix worktree → dev 대상 PR로 진행한다.
v1.1의 PR 병합·배포·학습·모델 활성화·권한 변경 승인은 각각 확인한다. 과거 AGENTS.md의
병합 상시 승인 문구로 최신 사용자의 승인 제한을 우회하지 않는다.
기준 dev `d3d790a`; #27의 CodingTaskSpec/CodingWorkspace는 완료한 기반이다.
## 현재 구현 PR

[PR #29](https://github.com/wjdwodhks35/Role-AI/pull/29)는 격리 탐색·반복 수정·명령 실행·
오류 복구·체크포인트·중단/재개·예산·최소 기록·작업 API/화면을 수직 연결한다.
R01 최소 계약 코드를 재사용하고, C01~C03에 해당하는 runtime 경로를 포함한다.
기존 DB를 바꾸지 않기 위해 R02의 DB 전용 저장 대신 사유 파일 checkpoint와 최소 JSONL 기록을
사용한다. 기존 PR #28은 별도로 병합하지 않았다.

완료 증거는 현재 revision의 실제 테스트 결과·누적 patch·작업 이력이다. scripted model+
실제 Docker의 실패→저장/중단→재개→재수정→통과를 CI에서 확인했다. 실모델·4060 Ti·
실브라우저 수동 확인은 남아 있다. 다음 확인 대상은 Coding Agent의 실사용 정확성·재개 안정성이며
다른 제품 기능을 자동으로 착수하지 않는다. [실행 안내](../coding-agent-runtime.md)를 참고한다.

## v1.1 장기 PR 계획 · 현재 범위 밖의 기능은 보류

아래 표는 통합 기획서의 장기 의존성을 보존한다. 현재 실행 순서나 전체 기능 구현 지시가 아니다.
특히 E01/P/S/M/K/O/L/A/U의 신규 제품 기능과 R02 DB 변경은 이번 PR 범위에서 제외한다.

| PR / 단계 | 의존성·범위 / 예정 파일 | 테스트·완료 기준 |
|---|---|---|
| P00 / 0 | 이번 계획 문서 5개, v1.1 기준선/경계 | 소스 근거·미측정 표시·의존성 확인. 운영 실측은 별도 승인/환경에서 수행 |
| R01 / 1 | Strategy 최소 schema·sanitizer·no-op/memory sink. 신규 strategy_events.py, test_strategy_events.py | unknown 필드/원문/args/경로 거부, ID·버전·enum·상한, 성공/실패/취소/미실행 구분. 추가 모델/네트워크 호출 0 |
| R02 / 1 | R01 후 Recorder 저장·별도 트랜잭션/동의/보존/삭제. models.py, migrations.py, backup 관련 테스트 | v3 fixture·세션·데이터 보존, 오프라인 migration/새 경로 복원, lock/full/중복/재시작/quota/tenant 격리. 운영 적용 별도 승인 |
| C01 / 1 | R01 후 승인된 저장소 테스트 runner와 Recorder 연계. code_execution.py, 신규 coding_runner.py | 실제 Docker 성공/실패/0개 테스트/timeout/OOM/출력·정리 실패. runner에 Git/socket/운영 볼륨 전달 금지. 기록 실패 시 결과 불변 |
| C02 / 1 | C01+R02 후 작업 API/영속성·모델 orchestrator·재수정. 신규 coding_jobs.py/coding_agent.py, main.py의 router 연결 | auth/CSRF/소유권, malformed 응답, 단계/시간/파일/재시도 한도, 취소/재시작, 승인 불일치·테스트 약화 탐지. attempt별 기록 |
| C03 / 1 | C02 후 작업 화면·diff·실패 이력·PR 후보. static/app.js/index.html 등 | 다른 소형 저장소의 버그/기능/해결 불가 E2E·독립 테스트, 승인 대기/오류/기록 불가 표시. 자동 push/merge 없음 |
| E01 / 1 후 | C03 후 실제 Coding Evaluation Agent. 신규 coding_evaluation.py, 평가 전용 데이터/worker | 정답 비공개, repo/문제 계열 분리, 수정 전후 고정 비교, 평가 오류 unknown, 보고서 hash/버전·재현성. 전략 선택 권한 없음 |
| P01 / 2 | C03 후 요구사항 ID·출처·acceptance·코드/테스트 매핑 | 버전/tenant/출처/미결정 보존, 누락/다름/검증 불가 판정 |
| P02 / 2 | P01 후 Preview L1/L2·부분 피드백 수정·동의한 Intent Gap 최소 기록 | 실제 브라우저 폼/전환/모바일, 모의 인증 운영 승격 금지, 코멘트 원문 기본 미저장 |
| S01 / 2 조건부 | 안정화된 C03+검증 경험 R02 후 Retriever 검색·근거 제안. 신규 strategy_retrieval.py | tenant/프로젝트/리비전/만료/삭제/검증 필터, 검증된 실패도 회수. 부족/stale/독성 데이터는 빈 결과, 명령 실행 0 |
| S02 / 2 조건부 | S01 후 검색 off/on 비교·제한적 연결·kill switch | 신규 repo/문제군, 동일 모델/예산/테스트, 성공/회귀/시간/자원/불확실성. 사전 기준 미충족이면 비활성 |
| M01~03 / 3 | P02 후 PDF 출처→OCR/이미지→영상/시각 QA 분리 | 페이지/프레임 provenance·신뢰도·추정, 대형/손상/주입·자원 제한, 미디어 경험도 동의/최소 기록 |
| K01~03 / 4 | 수집 어댑터→Raw/manifest·중복/누출→캐시/Hybrid/기억 평가 | revision 증분·권리·PII·소스 계열 split, Recall@K·캐시 무효화·정정 반영 |
| O01 / 5 | E01 완료+경험 품질 게이트 후 Optimizer 격리 비교 | 동일 base/model/tools/budget, 배정/seed/전략 버전 고정, 반복/구간 추정·회귀/자원. 작은 표본은 inconclusive |
| O02 / 5 | O01 후 Evolution Lab·제안/거부/사용자 승인/롤백 | 사례 검색→명령 실행 금지, 미승인 승격 차단, 즉시 비활성/이전 전략 복귀 |
| L01 / 5 후반 | 검증 경험→권리/PII/품질/중복/누출 심사→SFT/QLoRA 후보 manifest | 동의/검증 hash·소스 권리 확인. 최종셋/미검증/권리 불명 제외. export만으로 학습 시작 0 |
| L02 / 5 후반 | L01 후 별도 승인한 GPU 실험·독립 평가·후보 폐기/선택 | 기존 training/rlaif/lifecycle 재사용, 비학습 repo/문제군·모델/data hash·VRAM·회귀/롤백. 모델 활성화 별도 승인 |
| A01~ / 6 | 초기 권한/예산 유지; 후반 P0~P4 큐/SSE/상주·캐시 최적화 분리 | TTFT/p50/p95·VRAM·전환·대화 우선권·작업 기아·체크포인트 중단/재개 실측 |
| U01~ / 7 | 실제 상태 이벤트 후 AI Office·접근성·전체 회귀 | heartbeat/기록 오류/승인/연결 불명·재연결·키보드·모션·모바일, 허위 퍼센트 없음 |

## 초기 권장 순서와 조건

R01 → R02와 C01을 각각 검증 → C02 → C03 → E01.
R02의 운영 적용 미승인 시 memory/no-op로만 개발 검증하고 영속 기록 완료라고 표시하지 않는다.
누락된 과거 경험을 모델로 추정해 소급 작성하지 않는다.
Preview P01/P02는 이후 진행한다. S01/S02의 경험/평가 조건이 부족하면 검색만 보류한다.
E01 미완료 시 O01/O02는 활성화하지 않는다. 모델 학습은 L 단계까지 자동 연결하지 않는다.

## 공통 검사와 롤백

- 신규 테스트는 기존 Excel QA에 requirement ID·selector를 등록하고 순서/누락/스킵을 검증한다.
  기존 auth/CSRF/tenant/OpenClaw/migration/backup/workspace/runner 회귀·CI를 유지한다.
- Mock/임시 Git/실제 Docker/브라우저/실모델/실GPU 결과를 구분한다.
- R01은 no-op 복귀. R02는 기록 중지·데이터 보존 우선이며 자동 drop/downgrade하지 않는다.
  구버전 앱의 새 schema 호환을 별도 검증하고 필요 시 승인한 백업을 새 경로에 복원한다.
- C는 신규 코딩 진입 비활성화로 기존 대화/보안을 유지한다. S/O는 kill switch·이전 전략/
  검색 미사용 기준선으로 복귀한다. L은 운영 모델을 자동 교체하지 않는다.
