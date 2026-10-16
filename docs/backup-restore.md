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
