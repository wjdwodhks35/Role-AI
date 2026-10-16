"""Ordered, additive schema migrations; never silently ALTER or downgrade existing data."""
import argparse
import json

from sqlalchemy import inspect, text

from role_ai import models  # noqa: F401 -- register metadata
from role_ai.backup import create_backup, sqlite_path
from role_ai.config import Settings
from role_ai.database import Base, build_database

BASELINE = (
    'api_usage', 'auth_sessions', 'auto_approval_audits', 'auto_approval_policies',
    'benchmark_suites', 'candidate_selections', 'collection_plans', 'conversations',
    'dataset_snapshots', 'document_chunks', 'documents', 'memories', 'memory_history',
    'message_retrievals', 'messages', 'training_jobs', 'users',
)
MIGRATIONS = [(1, BASELINE), (2, ('worker_presence', 'worker_controls'))]


class MigrationRequired(ValueError):
    pass


def validate_tables(connection, names):
    inspector = inspect(connection)
    for name in names:
        if not inspector.has_table(name):
            raise MigrationRequired('필요한 테이블이 없습니다. 오프라인 마이그레이션을 확인하세요.')
        actual = {col['name']: col for col in inspector.get_columns(name)}
        expected = Base.metadata.tables[name]
        if set(actual) != set(expected.columns.keys()):
            raise MigrationRequired('기존 DB 열 구조가 다릅니다. 자동 변경하지 않습니다.')
        primary = inspector.get_pk_constraint(name)['constrained_columns']
        if set(primary) != {c.name for c in expected.primary_key.columns}:
            raise MigrationRequired('기존 DB 기본키 구조가 다릅니다.')
        for column in expected.columns:
            value = actual[column.name]
            if str(value['type']).upper() != str(column.type.compile(dialect=connection.dialect)).upper():
                raise MigrationRequired('기존 DB 열 타입이 다릅니다.')
            if bool(value['nullable']) != bool(column.nullable):
                raise MigrationRequired('기존 DB NULL 제약조건이 다릅니다.')
        actual_fk = {(tuple(f['constrained_columns']), f['referred_table'],
                      tuple(f['referred_columns']), f.get('options', {}).get('ondelete'))
                     for f in inspector.get_foreign_keys(name)}
        expected_fk = {(tuple(c.name for c in f.columns), f.referred_table.name,
                        tuple(e.column.name for e in f.elements), f.ondelete)
                       for f in expected.foreign_key_constraints}
        if actual_fk != expected_fk:
            raise MigrationRequired('기존 DB 외래키 구조가 다릅니다.')
        actual_indexes = {(tuple(i['column_names']), bool(i.get('unique')))
                          for i in inspector.get_indexes(name)}
        expected_indexes = {(tuple(c.name for c in i.columns), bool(i.unique)) for i in expected.indexes}
        if not expected_indexes.issubset(actual_indexes):
            raise MigrationRequired('기존 DB 인덱스가 다릅니다.')


def schema_status(connection):
    tables = set(inspect(connection).get_table_names())
    versions = []
    if 'schema_migrations' in tables:
        versions = list(connection.scalars(text('SELECT version FROM schema_migrations ORDER BY version')))
        if versions != list(range(1, len(versions) + 1)) or (versions and versions[-1] > MIGRATIONS[-1][0]):
            raise MigrationRequired('알 수 없거나 순서가 잘못된 DB 버전입니다. 변경하지 않습니다.')
    return {'current': versions[-1] if versions else 0, 'latest': MIGRATIONS[-1][0],
            'legacy': bool(tables - {'schema_migrations'}) and not versions}


def initialize_database(engine, *, allow_upgrade=False):
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            status = schema_status(connection)
            tables = set(inspect(connection).get_table_names()) - {'schema_migrations'}
            # Fresh DBs or pre-created complete test/import schemas can be initialized safely.
            complete = all(name in tables for _, names in MIGRATIONS for name in names)
            if tables and status['current'] < status['latest'] and not complete and not allow_upgrade:
                raise MigrationRequired('DB 업그레이드가 필요합니다. 중지·백업 후 python -m role_ai.migrations upgrade를 실행하세요.')
            if tables and not status['current']:
                validate_tables(connection, BASELINE)
            connection.exec_driver_sql('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
            for version, names in MIGRATIONS:
                if version > status['current']:
                    if tables and not allow_upgrade and not complete:
                        raise MigrationRequired('오프라인 마이그레이션이 필요합니다.')
                    Base.metadata.create_all(connection, tables=[Base.metadata.tables[name] for name in names])
                    validate_tables(connection, names)
                    connection.execute(text("INSERT INTO schema_migrations VALUES (:version, strftime('%Y-%m-%dT%H:%M:%fZ','now'))"), {'version': version})
                else:
                    validate_tables(connection, names)
            if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone():
                raise MigrationRequired('외래키 무결성 검사 실패')
            connection.commit()
            return {'current': MIGRATIONS[-1][0], 'state': 'ready'}
        except BaseException:
            connection.rollback()
            raise


def upgrade_offline(settings, backup_destination, *, offline=False):
    if not offline:
        raise MigrationRequired('API·모든 Worker 중지 확인 후 --offline이 필요합니다.')
    path = sqlite_path(settings)
    if path.exists():
        create_backup(settings, backup_destination, offline=True)
    engine, _ = build_database(settings.database_url)
    try:
        return initialize_database(engine, allow_upgrade=True)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description='SQLite 버전 확인·백업 후 순차 업그레이드')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    upgrade = sub.add_parser('upgrade')
    upgrade.add_argument('--offline', action='store_true')
    upgrade.add_argument('--backup', required=True)
    args = parser.parse_args()
    settings = Settings()
    try:
        if args.action == 'upgrade':
            result = upgrade_offline(settings, args.backup, offline=args.offline)
        elif not sqlite_path(settings).exists():
            result = {'current': 0, 'latest': MIGRATIONS[-1][0], 'state': 'not_initialized'}
        else:
            engine, _ = build_database(settings.database_url)
            try:
                with engine.connect() as connection:
                    result = schema_status(connection)
            finally:
                engine.dispose()
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError):
        parser.exit(2, '마이그레이션 실패. 백업은 보존되며 DB 상태·구조를 확인해야 합니다.\n')


if __name__ == '__main__':
    main()
