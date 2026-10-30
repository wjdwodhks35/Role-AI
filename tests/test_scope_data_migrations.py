"""v4→v5 synthetic-only: preserve all old rows and do not bind old conversations."""

import hashlib
import pytest
from sqlalchemy import inspect, text
from role_ai.database import Base, build_database
from role_ai.models import User, Document, Conversation
from role_ai.migrations import MIGRATIONS, MigrationRequired, initialize_database, schema_status
from role_ai.scope_schema import install_scope_guards
from role_ai.scope_data_schema import DATA_SCOPE_TABLES
from role_ai.project_scopes import classify_legacy_resources


@pytest.fixture
def version4(tmp_path):
    engine, factory = build_database(f"sqlite:///{tmp_path / 'v4.db'}")
    names = [name for v, tables in MIGRATIONS if v <= 4 for name in tables]
    with engine.begin() as db:
        Base.metadata.create_all(db, tables=[Base.metadata.tables[n] for n in names])
        db.exec_driver_sql(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version in range(1, 5):
            db.execute(text("INSERT INTO schema_migrations VALUES(:v,'synthetic')"), {"v": version})
        install_scope_guards(db)
    with factory() as db:
        db.add(User(id="owner", email="owner@test.invalid", password_hash="synthetic"))
        db.flush()
        db.add(
            Document(
                id="old-document",
                user_id="owner",
                filename="private.txt",
                content="private legacy",
                digest=hashlib.sha256(b"private legacy").hexdigest(),
                project="",
            )
        )
        db.add(Conversation(id="old-conversation", user_id="owner", title="A is only a title"))
        db.commit()
    with engine.begin() as db:
        classify_legacy_resources(db)

    def snapshot():
        with engine.connect() as db:
            return {
                name: sorted(
                    [tuple(row) for row in db.execute(Base.metadata.tables[name].select())],
                    key=repr,
                )
                for name in names
            }

    yield engine, names, snapshot
    engine.dispose()


def test_v5_adds_only_empty_boundary_tables_and_preserves_v4(version4):
    engine, _, snapshot = version4
    before = snapshot()
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    with engine.connect() as db:
        assert schema_status(db)["current"] == 4
        assert not (set(DATA_SCOPE_TABLES) & set(inspect(db).get_table_names()))
    assert initialize_database(engine, allow_upgrade=True)["current"] == MIGRATIONS[-1][0]
    assert snapshot() == before
    with engine.connect() as db:
        for name in DATA_SCOPE_TABLES:
            assert db.execute(Base.metadata.tables[name].select()).first() is None
    assert initialize_database(engine)["current"] == MIGRATIONS[-1][0]
    assert snapshot() == before


def test_v5_failure_rolls_back_new_ddl_and_version_without_losing_v4(version4, monkeypatch):
    from role_ai import migrations

    engine, _, snapshot = version4
    before = snapshot()
    original = migrations.validate_data_guards

    def fail(db):
        original(db)
        raise ValueError("synthetic post-DDL failure")

    monkeypatch.setattr(migrations, "validate_data_guards", fail)
    with pytest.raises(MigrationRequired):
        initialize_database(engine, allow_upgrade=True)
    assert snapshot() == before
    with engine.connect() as db:
        assert schema_status(db)["current"] == 4
        assert not (set(DATA_SCOPE_TABLES) & set(inspect(db).get_table_names()))
    monkeypatch.setattr(migrations, "validate_data_guards", original)
    assert initialize_database(engine, allow_upgrade=True)["current"] == MIGRATIONS[-1][0]


@pytest.mark.parametrize("damage", ["trigger", "check"])
def test_v5_integrity_drift_is_denied_without_repair(version4, damage):
    engine, _, snapshot = version4
    initialize_database(engine, allow_upgrade=True)
    before = snapshot()
    with engine.begin() as db:
        if damage == "trigger":
            db.exec_driver_sql("DROP TRIGGER scope_archives_no_delete")
        else:
            db.exec_driver_sql("PRAGMA writable_schema=ON")
            db.exec_driver_sql(
                "UPDATE sqlite_master SET sql=replace(sql,'generation >= 1','generation >= 0') WHERE name='scope_conversation_bindings'"
            )
            db.exec_driver_sql("PRAGMA writable_schema=OFF")
            db.exec_driver_sql("PRAGMA schema_version=999")
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    assert snapshot() == before
