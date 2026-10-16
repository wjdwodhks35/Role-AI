# DB 버전 관리와 오프라인 마이그레이션

SQLite에 schema_migrations 이력을 추가합니다. v1은 기존 17개 테이블의 기준 구조입니다.
새 DB는 초기화하고 완전한 기존 기준 DB는 버전을 등록합니다. 테이블·열·타입·NULL·PK·FK·인덱스를
검증하며 지원하지 않는 구조 차이/미래 버전은 거부합니다. create_all은 기존 열을 고치는 수단이 아닙니다.

```bash
python -m role_ai.migrations status
# API·수집·학습 Worker를 모두 중지한 후
python -m role_ai.migrations upgrade --offline --backup /안전한/새-업데이트전-백업경로
```

기존 DB가 있으면 반드시 새 경로에 DB+artifact 백업을 검증한 뒤 순서대로 적용합니다.
DB가 없으면 새로 초기화합니다. 이미 존재하는 백업 경로는 거부하므로 매 실행마다 새 경로를 사용하세요.
업그레이드 실패 시 해당 DB 변경을 rollback하고 백업은 보존합니다. 지원하지 않는 다운그레이드나
기존 열 삭제/형식 변환을 자동 수행하지 않습니다. 필요하면 검증된 백업을 새 경로로 복원하세요.
실행 중 서비스에 마이그레이션하지 마세요. --offline은 사용자의 서비스 중지 확인입니다.

API와 Worker의 시작 시 현재 버전·구조를 검사합니다. 향후 버전이 추가되면 기존 DB는
위 CLI로 백업 후 업그레이드해야 하며, 기동 중 임의의 열 변경을 수행하지 않습니다.

현재 최신은 v2이며 Worker presence/control 테이블을 추가합니다. v1 DB는 오프라인 upgrade가 필요합니다.
