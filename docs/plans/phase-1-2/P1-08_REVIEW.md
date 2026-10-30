# P1-08 Phase 1 통합 검증

시작 dev `06900f1e52ca671896784c17ef856ac8c6a7f097` (P1-07 #41), feature/p1-08-integration-verification.
현재 문서는 로컬 검증/PR 검토 기록이다. CI 전체·dev 병합 확인 전에는 Phase 1 완료가 아니다.

## 수용 기준 추적

| 단계 | 검증 경로 | 확인 범위 |
|---|---|---|
| P1-01 #34 | test_task_contracts / Coding contract tests | 불변 계약·digest·입력 검증·Coding 호환, 상태는 권한 아님 |
| P1-02 #36 | test_project_scopes / test_scope_migrations | 개인 보관함·정확 버전 Grant·파생 출처·추가형 이행/보존/복구 |
| P1-03 #37 | test_scope_permissions / test_scoped_reads | 실제 세션/소유권·정책 세대·현재 자료 적격성·fail-closed |
| P1-04 #38 | test_scope_data_paths / migrations + client QA | 목록/검색/직접 ID/문맥·보관·신규 대화 고정·과거 기록 보존 |
| P1-05 #39 | test_task_approvals | 인증/CSRF·정확 계약·도구·현재 승인/철회·WAL stale snapshot 거부 |
| P1-06 #40 | test_task_checkpoints | 상태 CAS/lock·변조/저장 실패·중단 후 명시 재개·제어 |
| P1-07 #41 | test_task_resources | 제한 retry/예약·실측 분리/시간·단계·토큰/GPU admission·무재실행 복구 |
| P1-08 | test_phase_one_integration | 실제 자료 read→runtime→독립 digest 확인→완료, 권한 우회/철회/backup fault |

## 새로 확인한 gap과 보완

1. 기존 backup v1은 DB/artifacts이며 `coding_root/contracts` 공통 상태/ledger는 빠질 수 있었다.
   새 `task_backup` companion은 v1을 그대로 application/에 포함하고 common metadata를 task-checkpoints/에 별도 보존한다.
   기존 backup/restore 형식·DB schema·운영 경로는 바꾸지 않는다.
2. root 설정이 달라진 환경에서 과거 승인으로 새 상태 파일을 시작할 수 있었다.
   coding_root/storage_dir 경로 hash를 승인 environment에 바인딩해 설정 변경 시 기존 승인 무효화.
3. backup/gpu 정책 코드는 실제 보호 경계다. 기존 Coding MVP 모델 수정 제외 목록에 두 파일을 추가했다.
   새 task_backup은 기존 task_* 보호 규칙을 그대로 따른다.

Companion은 API/worker가 모두 중지된 offline 환경을 명시적으로 요구한다.
기존 create lock과 각 작업 execute/runtime/control/task lock, copy 전후 목록·hash/계약·사용자 경로 검증을 재사용한다.
활성 작업/복사 중 변경/알 수 없는 파일·orphan/symlink/손상은 자동 repair 없이 거부한다.
원문이나 ledger를 지우지 않고 새 백업/새 비활성 복원 경로만 허용한다.
pending intent·보수 예약·Grant/Task 승인 철회 표식이 보존되고 자동 replay/설정 전환/모델 실행은 없다.

파일 checksum과 namespace/계약 검증은 현재 실행 승인이나 OS 서명이 아니다.
복원본을 실제 사용하기 전에는 TaskRuntime의 profile/counter·P1-05 현재 인증/권한을 다시 검증해야 한다.
기존 Coding Agent의 별도 candidate/worktree 전체, 외부 모델/volume/시크릿을 이 companion이 모두 백업한다는 뜻은 아니다.
운영 이행·백업 활성화/오래된 세션/Grant 재활성화 정책은 DEC-015 별도 승인으로 남긴다.

## 10 Pass 자체 검토

06_10회_재검토_체크리스트 적용. 자체 검토이며 실제 독립 모델 평가/LLM 성능 검증이 아니다.

| Pass | 새 발견·수정 | 잔여 위험 |
|---:|---|---|
| 1 원 요구 | 단계별 성공만으로 통합 증거 부족 → 계약/실제 DB 권한/tool/runtime/독립 규칙 확인을 한 흐름으로 검증 | 실제 LLM Coding 품질은 P2 |
| 2 모순 | DB 복원만으로 새 task 예산/intent 유실 가능 → 기존 v1+common companion, 자동 replay 없음 | 별도 legacy Coding candidate 저장소까지 포함하지 않음 |
| 3 기술 사실 | script/합성 GPU를 실모델 성공으로 오인 가능 → 실제 데이터 digest 검증과 simulated 실행을 구분 | 실제 토큰/VRAM 미측정 |
| 4 현실성 | dirty 원본/미병합 PR을 건드릴 위험 → 최신 dev 독립 worktree, #31/#32/#33 비통합 | 공유 hub는 draft 참고 상태 |
| 5 아키텍처 | 기존 백업 형식 변경이 옛 복원을 깨뜨릴 위험 → v1 application 그대로 포함, 별도 companion 모듈/CLI | operator가 실제 settings/root를 제공해야 함 |
| 6 보안/복구 | 과거 DB의 세션/Grant를 활성화하면 철회 정책 역행 가능 → 새 비활성 경로만, 운영 활성화 분리 | DEC-015 실제 활성화 절차 별도 승인 |
| 7 자원/성능 | 단일 작업 lock만으로 일관 백업 불충분 → create와 모든 common 작업 lock/변경 감지 | 전체 API/worker offline 확인은 운영자 전제 |
| 8 결과 예측 | 복사 중 실패가 원본/기존 대상 손상 가능 → 새 임시 묶음 검증 후 게시, 기존 대상 거부·실패 cleanup | OS 장애 후 임시 파일 수동 점검 필요 |
| 9 적대 반론 | 저장 경로 변경으로 카운터 reset/보안 코드 수정 우회 → root 환경 지문·backup/GPU 보호 경로 부정 검사 | 악성 OS 소유자 방어는 범위 밖 |
| 10 수정 후 통합 | 새 companion이 기존 데이터/이행·인증을 깨뜨릴 위험 → 기존 v1/추가형 migration 회귀와 source JSON/원문 보존 | CI/dev 병합 전 미완료 |

## 실제 검증과 한계

- 관련 통합/자원/이행/기존 backup 54 PASS. 신규 흐름/보관 묶음은 합성 SQLite와 임시 파일만 사용.
- 실제 flock 활성 lease 거부, 원본 JSON 보존, pending 예약 및 철회 tombstone 복원, 손상/extra 파일/복사 중 writer/중첩·기존 경로 거부를 검사.
- 실모델 호출·운영 DB/서비스·환경변수·시크릿·main 변경 없음. UI 변경 없음.
- 기존 Coding Agent/API의 별도 로컬 소유자 승인·격리 경계는 유지한다. 공통 계약 경로의 자동 worker/실제 위임 연결은 P2이며 모델이 legacy API를 우회 호출하도록 활성화하지 않는다.
- CLI --help만 확인했으며 운영 경로에서 create/restore를 실행하지 않았다.
- 최종 전체 QA/지문/CI/PR/merge는 확인 후 추가한다.

롤백은 새 companion/통합 경로 호출 중단 및 코드 revert다. DB schema는 v6 그대로이고 기존 v1 복원은 유지된다.
새 기록/백업을 지우거나 예산을 reset하지 않는다. 운영 가동/복원 활성화는 별도 승인이다.

## 최종 로컬 결과

- 전체 합성 Excel QA 430 시나리오·830회 PASS, ordered=true, exit0.
- 기존417개 계획 행 보존, 새13개 통합 시나리오·17회. 추가 루트 경로/복사 race/활성 lease/철회 tombstone/중첩 새 복원 경로 검증 포함.
- `/tmp/role-ai-p108-verified-7qf7g_72` Python/QA123개 파일 지문이 최종 코드와 일치.
- socket·Docker/Ollama/GPU 명령 차단, 별도 HOME/TMPDIR·합성 DB. 실제 모델/운영 검증 아님.
- 관련54 PASS 및 최종 통합17 PASS, 전체 Ruff/diff 검사 통과. 기존 Starlette/httpx warning1.
- 공유 기획 PR #33 head1435db5e…가 그대로이며 다른 미병합 PR은 보존했음을 재확인.
- CI/PR/dev 병합 확인 후 외부 완료 기록에 실제 merge SHA와 Phase 1 완료 여부를 기록한다.
