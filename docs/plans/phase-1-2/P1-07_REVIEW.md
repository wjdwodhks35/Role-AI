# P1-07 실패 복구·자원 관리 검토

시작 dev `dc7fde777b24cc4fc147a4158861bc9b1dcefc4b` (P1-06 #40), feature/p1-07-runtime-budgets.
공통 계약/digest와 기존 Coding Agent의 파일·승인·격리는 유지한다. 미병합 PR을 통합하지 않는다.

## 구현과 경계

- `TaskRuntime`: 신뢰된 서버 callback만 받는 async 경계. HTTP 실행 경로·모델 제공 callback·호스트 명령은 없다. 실제 도구 인자/파일 제약은 P1-05 dispatcher 또는 기존 검증된 runner가 책임진다.
- 현재 인증/계약/프로젝트/정책/자료·도구 승인과 running 상태를 매 attempt 전후 확인. 취소/철회 이후 결과 사용 차단. 원본 데이터/코드는 수정하지 않는다.
- 기존 ModelBusy/BudgetStop/Recoverable/CodingBoundaryError/GpuBudgetError 분류 재사용. 확인된 busy/안전한 recoverable만 최대 계약 retries 이내 재시도, 시도 자체도 steps를 소비한다. 권한·범위·unknown·timeout은 자동 재시도하지 않는다.
- 불변 서버 RuntimeLimits(기본 token budget32768, context4096/output1024, hard bound65536/16384/2048)와 계약 max_steps/retries/timeout 결합. 모델 JSON으로 정책을 지정하지 않는다. profile digest가 달라지면 저장 ledger 재사용 거부.
- 호출 전 최대 context+output 토큰을 예약해 원자 기록. transport가 별도로 보고한 정수 usage만 측정 합계에 정산. 없으면 예약을 유지하고 model_tokens=None으로 표시한다. 예약/추정치를 실제 Codex/모델 측정으로 표현하지 않는다.
- `runtime.json`은 기존 private/checksum/fsync/replace/lock 재사용. intent를 먼저 저장, 결과 불명확한 중단은 자동 replay 금지. explicit recover도 예약을 환불하지 않으며 기록과 횟수/예산을 보존한다. 동일 operation ID를 다시 실행하지 않는다.
- 오류 원문·prompt·소스·응답·command args는 ledger에 저장하지 않고 서버 생성 ID/고정 분류/측정 여부/카운터만 기록. 실제 결과의 영속 저장은 기존 runner/후속 P2 artifact 경로가 담당한다.
- 전체 lifetime deadline과 monotonic active duration, asyncio bounded await 검사. callback은 취소 가능한 bounded I/O여야 하며 취소를 무시하는 임의 Python/비협조 코드의 강제 종료를 보장하지 않는다. 실제 비신뢰 코드는 기존 격리 runner에서만 실행한다.
- GPU는 기존 gpu_snapshot/admission/read_policy 재사용, 설정 파일은 변경하지 않는다. 기본6GiB(최대7GiB), 최소1GiB reserve와 기존의 더 엄격한 정책을 결합한다. 미측정/과다 사용은 시작하지 않고 waiting. 실행 후 overrun/측정 이상은 결과 폐기. 이미 resident인 모델을 공짜로 재사용한다고 가정하지 않는다.
- GPU admission은 물리 VRAM/시스템 전체 hard cap이 아니다. 실제 peak 계측은 P2-D. 모델 신규 설치·전체 unload·프로세스 종료·운영 GPU 설정 변경 없음. GPU 정책도 승인 environment digest에 포함해 변경 시 과거 승인 무효화.

## 10 Pass 자체 검토

06_10회_재검토_체크리스트 적용. 독립 평가/실제 LLM 성능 검증이 아니다.

| Pass | 새 발견·수정 | 잔여 위험 |
|---:|---|---|
| 1 원 요구 | 기존 오류/예산 코드는 Coding 전용 → 공통 계약·상태/현재 승인과 결합한 adapter | 실제 로컬 위임은 P2 |
| 2 모순 | 성공 재시도로 중단 결과 덮기 위험 → pending intent 복구는 no-replay/보수 예약 유지 | 결과 본문 복원은 callback artifact 필요 |
| 3 기술 사실 | 토큰 추정·실측 혼동 → 측정 합계와 예약/미측정·model_tokens=None 분리 | Codex 토큰은 이 모듈에서 측정하지 않음 |
| 4 현실성 | GPU 실제 측정 불가 시 0 가정 위험 → None/이상/초과 거부, 합성 probe로 검증 | 실제 하드웨어 검증 P2 |
| 5 구조 | 기존 GPU 설정을 몰래 활성화할 위험 → 파일 변경 없는 실행별 보수 admission | 기존 resident 모델 때문에 대기할 수 있음 |
| 6 보안/복구 | 원문 예외 로그에 비밀 저장 가능 → enum/카운터만 ledger, 현재 권한 재검사 | 신뢰된 callback의 외부 결과 저장 정책 필요 |
| 7 자원/성능 | 무한 retry/다른 ID로 예산 reset 가능 → 모든 시도 steps/보수 토큰 소비·immutable profile | 시스템 전체 GPU hard cap은 아님 |
| 8 결과 예측 | timeout 후 요청이 끝났다고 가정 → 불명확 예약 유지·중단 표시·자동 재시도 금지 | 실제 외부 요청 종료 확인 필요 |
| 9 적대 반론 | 새 runtime 또는 profile로 카운터 초기화 우회 → 계약/profile/steps/events/pending 검증, 같은 파일 재사용 | 악성 OS 소유자에 대한 서명 방어는 아님 |
| 10 수정 후 통합 | 테스트용 global clock이 승인시각까지 바꾸는 flake 발견 → runtime clock만 격리 주입, 인증 검증은 유지 | 전체 회귀/CI 완료 전 미완료 |

## 검증 및 롤백

- 초기 runtime/상태/승인69 PASS, 기존 Coding runtime/안정성/GPU 포함93 PASS, 최종 runtime22 PASS.
- busy 제한 재시도/원문 비저장, unknown 및 범위 오류 무재시도, usage 실측/미측정 구분, 단계/토큰/전체 deadline 차단, timeout 취소 cleanup 및 예약 유지, GPU 미측정/초과·post overrun 거부, 철회 결과 폐기, pending explicit 복구/무환불, runtime 변조 무재설정 확인.
- GPU 숫자와 usage는 합성 fixture 입력이며 실제 모델 토큰/실측 VRAM 결과로 주장하지 않는다.
- 운영 데이터/DB/서버/설정/모델 미변경, 실제 모델·외부 유료 API 미실행.
- 롤백: 새 runtime 호출 중단/코드 revert, runtime/공통 checkpoint를 삭제하거나 카운터를 reset하지 않는다. DB downgrade 없음. 불명확한 실제 요청의 운영 정리는 별도 승인이다.
- 최종 전체 회귀·소스 지문·CI·PR/dev 병합은 확인 후 기록한다.

## 최종 로컬 결과

- 합성 Excel QA 417 시나리오·813회 PASS, ordered=true, exit0.
- 기존402개 계획 행 보존, 새15개 시나리오·22회. updater 재실행0.
- `/tmp/role-ai-p107-verified-yjy1z_9n` 복사본 Python/QA121개 지문 일치.
- 실제 socket·Docker/Ollama/GPU 명령 차단, 별도 HOME/TMPDIR/합성 DB. Ruff 전체/diff 통과.
- 기존 Starlette/httpx deprecation warning1. 실제 토큰·VRAM 측정이나 실제 모델 평가 결과는 아니다.
- CI 전부/충돌·리뷰/dev 병합 확인 전에는 완료로 처리하지 않는다.
