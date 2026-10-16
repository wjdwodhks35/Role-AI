import pytest
from sqlalchemy import inspect, text

from role_ai.backup import verify_backup
from role_ai.config import Settings
from role_ai.database import Base, build_database
from role_ai.migrations import MigrationRequired, initialize_database, schema_status, upgrade_offline


def test_schema_initialization_idempotent_and_legacy(tmp_path):
    engine, _ = build_database(f'sqlite:///{tmp_path / "db"}')
    assert initialize_database(engine)['current'] == 1
    assert initialize_database(engine)['current'] == 1
    with engine.begin() as db:
        db.execute(text('DROP TABLE schema_migrations'))
        db.execute(text("INSERT INTO users VALUES('owner','owner@test','hash',1,1)"))
    assert initialize_database(engine)['current'] == 1
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM users')) == 1
        assert schema_status(db)['current'] == 1
    engine.dispose()


@pytest.mark.parametrize('damage', ['column', 'future', 'missing_table', 'index'])
def test_schema_rejects_drift_without_data_loss(tmp_path, damage):
    engine, _ = build_database(f'sqlite:///{tmp_path / "db"}')
    initialize_database(engine)
    with engine.begin() as db:
        if damage == 'column':
            db.execute(text('ALTER TABLE users ADD COLUMN unknown TEXT'))
        elif damage == 'future':
            db.execute(text('INSERT INTO schema_migrations VALUES(99, "future")'))
        elif damage == 'index':
            db.execute(text('DROP INDEX ix_users_email'))
        else:
            db.execute(text('DROP TABLE message_retrievals'))
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    if damage == 'column':
        with engine.connect() as db:
            assert 'unknown' in {c['name'] for c in inspect(db).get_columns('users')}
    engine.dispose()


def test_offline_upgrade_backs_up_and_rolls_back(tmp_path, monkeypatch):
    from role_ai import migrations
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "live.db"}',
                        storage_dir=str(tmp_path / 'artifacts'))
    engine, _ = build_database(settings.database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    with pytest.raises(MigrationRequired):
        upgrade_offline(settings, tmp_path / 'no-approval')
    result = upgrade_offline(settings, tmp_path / 'backup', offline=True)
    assert result['current'] == 1
    verify_backup(tmp_path / 'backup')
    engine, _ = build_database(settings.database_url)
    original = migrations.validate_tables
    def fail_after_ddl(connection, names):
        original(connection, names)
        if names == ('users',):
            connection.exec_driver_sql('CREATE TABLE must_rollback (id INT)')
            raise MigrationRequired('simulated failure')
    monkeypatch.setattr(migrations, 'MIGRATIONS', [(1, migrations.BASELINE), (2, ('users',))])
    monkeypatch.setattr(migrations, 'validate_tables', fail_after_ddl)
    with pytest.raises(MigrationRequired):
        initialize_database(engine, allow_upgrade=True)
    with engine.connect() as db:
        assert 'must_rollback' not in inspect(db).get_table_names()
        assert list(db.scalars(text('SELECT version FROM schema_migrations'))) == [1]
    engine.dispose()
