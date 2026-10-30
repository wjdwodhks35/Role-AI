# 오프라인 백업·새 경로 복원

API·수집·학습 Worker를 모두 중지한 뒤 실행하세요. `--offline`은 중지 확인이며
실제 프로세스를 자동 종료하지 않습니다. 저장소/수집 잠금과 SQLite 쓰기 잠금도 확인합니다.
백업에는 SQLite 일관된 snapshot과 artifact(학습 adapter 포함), 파일별 크기·SHA-256이 포함됩니다.
설정 `.env`, 외부 기반 모델, 가상환경, 다른 Docker volume은 포함하지 않습니다.
DB에는 개인정보·인증 세션이 있으므로 백업을 비밀 자료로 보관하고 Git에 올리지 마세요.

```bash
python -m role_ai.backup create /안전한/새-백업경로 --offline
python -m role_ai.backup verify /안전한/새-백업경로
python -m role_ai.backup restore /안전한/새-백업경로 /새-복원경로
```

대상 경로가 존재하면 거부합니다. 기존 DB·파일·volume은 덮어쓰거나 삭제하지 않습니다.
실패한 작업이 생성한 임시 출력만 정리합니다. 심볼릭 링크·특수 파일·누락/추가/변조를 거부합니다.
manifest는 서명되지 않았으므로 출처를 신뢰하는 백업만 사용하세요. 복원 폴더에서 동일 해시와
SQLite integrity/foreign-key 검사를 수행하며, 완료해도 서비스를 자동 전환하지 않습니다.
복원할 때에는 원본 DB 파일이 아닌 `database.sqlite`와 `artifacts` 경로를 설정해 별도로 기동하고
대화·기억·문서·학습 snapshot을 확인한 후 전환하세요. 외부 모델의 경로/지문도 별도 확인해야 합니다.

Compose 데이터는 호스트에서 같은 volume의 실제 DB/artifact를 읽을 수 있는 점검 환경이 필요합니다.
앱 volume을 임의로 삭제하거나 초기화하지 마세요. 백업 경로는 artifact 밖에 두고 충분한 공간을 확보하세요.
이 기능은 오프라인 CLI이며 실행 중인 서비스의 무중단 백업을 보장하지 않습니다.


## 공통 Task 체크포인트 포함 (P1-08)

기존 `role_ai.backup` v1 묶음은 DB와 `storage_dir` artifacts를 보존한다.
`coding_root/contracts`의 공통 상태/runtime/control 기록은 이 묶음 밖에 있을 수 있다.
공통 작업이 존재할 때는 `role_ai.task_backup` companion을 사용한다. 기존 v1 구조/복원은 변경하지 않는다.

운영 실행·모든 API/worker 중지·복원 활성화는 별도 승인 후 수행해야 한다. 이번 개발 검증은 합성 임시 DB만 사용했다.
승인된 실제 오프라인 환경에서 사용할 명령은 다음과 같다(현재 서버에서 실행한 명령이 아니다).

```bash
python -m role_ai.task_backup create /새로운/백업경로 --offline
python -m role_ai.task_backup verify /새로운/백업경로
python -m role_ai.task_backup restore /새로운/백업경로 /새로운/비활성복원경로
```

묶음에는 `application/`(기존 v1 DB/artifacts)와 `task-checkpoints/`(common 상태/ledger/control JSON)가 있다.
기존 Coding Agent의 별도 candidate/worktree/checkpoint 전체나 외부 모델·시크릿·Docker volume을 모두 백업한다는 뜻은 아니다.
활성 작업 잠금·복사 중 변경·알 수 없는 파일·손상/소유권 경로를 거부하며 원본을 고치거나 덮지 않는다.
복원은 새 경로만 가능하고 settings/서비스를 바꾸지 않으며 `activation=not_activated`, `automatic_replay=false`이다.

pending intent와 보수적 예약, 과거 계약/승인/Grant 철회 표식을 그대로 보존한다.
복원 검증이 현재 권한이나 실행 승인을 주지 않는다. 실제 가동 전에는 최신 인증·정책 세대/철회 기록·저장 경로·runtime profile을 검토하고 새 승인을 받아야 한다.
오래된 백업에서 유효했던 세션/Grant를 운영에 다시 활성화할 절차는 DEC-015의 별도 설계·승인 사항이다.
GPU·runtime 숫자/원문·예산을 재설정하거나 손상된 기록을 자동 repair하지 않는다.
