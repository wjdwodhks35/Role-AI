"""v5→v6 exclusively on synthetic SQLite, preserving snapshots and rollback."""

import pytest
from sqlalchemy import inspect, text
from role_ai.database import Base, build_database
from role_ai.migrations import MIGRATIONS, initialize_database, MigrationRequired, schema_status
from role_ai.scope_schema import install_scope_guards
from role_ai.scope_data_schema import install_data_guards
from role_ai.models import User, Conversation
from role_ai.backup import create_backup, restore_backup
from role_ai.config import Settings


@pytest.fixture
def previous(tmp_path):
    path = tmp_path / "previous.db"
    engine, factory = build_database(f"sqlite:///{path}")
    names = [n for v, ns in MIGRATIONS if v <= 5 for n in ns]
    with engine.begin() as db:
        Base.metadata.create_all(db, tables=[Base.metadata.tables[n] for n in names])
        db.exec_driver_sql(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for v in range(1, 6):
            db.execute(text("INSERT INTO schema_migrations VALUES(:v,'synthetic')"), {"v": v})
        install_scope_guards(db)
        install_data_guards(db)
    with factory() as db:
        db.add(User(id="owner", email="owner@test.invalid", password_hash="synthetic"))
        db.flush()
        db.add(Conversation(id="legacy", user_id="owner", title="retained"))
        db.commit()

    def snapshot():
        with engine.connect() as db:
            return {
                n: sorted(
                    [tuple(r) for r in db.execute(Base.metadata.tables[n].select())], key=repr
                )
                for n in names
            }

    yield engine, path, snapshot
    engine.dispose()


def test_v6_requires_explicit_upgrade_preserves_and_repeats(previous):
    engine, _, snapshot = previous
    before = snapshot()
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    assert snapshot() == before
    assert initialize_database(engine, allow_upgrade=True)["current"] == 6
    assert snapshot() == before
    assert initialize_database(engine)["current"] == 6
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM task_approvals")).scalar() == 0


def test_v6_failed_guard_validation_rolls_back_all_new_ddl(previous, monkeypatch):
    from role_ai import migrations

    engine, _, snapshot = previous
    before = snapshot()

    def fail(_):
        raise ValueError("synthetic failure")

    monkeypatch.setattr(migrations, "validate_approval_guards", fail)
    with pytest.raises(MigrationRequired):
        initialize_database(engine, allow_upgrade=True)
    assert snapshot() == before
    with engine.connect() as db:
        assert schema_status(db)["current"] == 5
        assert "task_approvals" not in inspect(db).get_table_names()


def test_v6_guard_drift_never_auto_repairs(previous):
    engine, _, snapshot = previous
    initialize_database(engine, allow_upgrade=True)
    before = snapshot()
    with engine.begin() as db:
        db.exec_driver_sql("DROP TRIGGER task_approval_no_delete")
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    assert snapshot() == before


def test_v5_backup_restores_to_separate_inactive_destination(previous, tmp_path):
    engine, path, snapshot = previous
    before = snapshot()
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{path}", storage_dir=str(tmp_path / "artifacts")
    )
    backup = tmp_path / "backup"
    create_backup(settings, backup, offline=True)
    initialize_database(engine, allow_upgrade=True)
    target = tmp_path / "restored"
    result = restore_backup(backup, target)
    assert result["activation"] == "not_activated"
    restored, _ = build_database(f"sqlite:///{target / 'database.sqlite'}")
    with restored.connect() as db:
        assert schema_status(db)["current"] == 5
        assert {
            n: sorted([tuple(r) for r in db.execute(Base.metadata.tables[n].select())], key=repr)
            for n in before
        } == before
    restored.dispose()
