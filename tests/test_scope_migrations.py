"""v3 synthetic data preservation, additive DDL rollback and copy-only recovery."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text

from role_ai.backup import restore_backup, verify_backup
from role_ai.config import Settings
from role_ai.database import Base, build_database
from role_ai.migrations import MIGRATIONS, MigrationRequired, initialize_database, schema_status
from role_ai.models import (
    Conversation, Document, Memory, Message, ProjectReadGrant, ResourceVersion, ScopedResource, User,
)
from role_ai.project_scopes import ScopeRepository, classify_legacy_resources
from role_ai.scope_migration import prepare_scope_copy
from role_ai.scope_schema import SCOPE_TABLES


@pytest.fixture
def legacy(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'legacy.db'}",
                        storage_dir=str(tmp_path / 'artifacts'), coding_root=str(tmp_path / 'coding'))
    engine, factory = build_database(settings.database_url)
    old_tables = [n for v, names in MIGRATIONS if v <= 3 for n in names]
    with engine.begin() as connection:
        Base.metadata.create_all(connection, tables=[Base.metadata.tables[n] for n in old_tables])
        connection.exec_driver_sql("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for v in (1, 2, 3):
            connection.execute(text("INSERT INTO schema_migrations VALUES(:v,'synthetic')"), {"v": v})
    with factory() as db:
        db.add(User(id="owner", email="owner@test.invalid", password_hash="synthetic"))
        db.flush()
        db.add(Conversation(id="old-chat", user_id="owner", title="A is only a title", revision=1))
        db.flush()
        db.add(Message(id="old-message", conversation_id="old-chat", role="user", sequence=0,
                       content="preserve this message", provider="mock", model="mock", skill="assistant"))
        db.flush()
        db.add_all([
            Document(id="personal", user_id="owner", filename="private.txt", project="",
                     content="personal", digest=hashlib.sha256(b"personal").hexdigest()),
            Document(id="project", user_id="owner", filename="project.txt", project="A",
                     content="project", digest=hashlib.sha256(b"project").hexdigest()),
            Document(id="bad-digest", user_id="owner", filename="bad.txt", project="A",
                     content="still preserved", digest="0" * 64),
            Memory(id="private-memory", user_id="owner", project="", kind="user", content="private memory",
                   revision=1, state="confirmed"),
            Memory(id="old-derived", user_id="owner", project="A", kind="knowledge", content="old derived",
                   source_message_id="old-message", revision=1, state="confirmed"),
        ])
        db.commit()
    Path(settings.storage_dir).mkdir()
    (Path(settings.storage_dir) / "synthetic.txt").write_text("artifact preserved")
    Path(settings.coding_root).mkdir()
    (Path(settings.coding_root) / "checkpoint.json").write_text('{"synthetic":"unchanged"}')
    yield settings, engine, factory, old_tables
    engine.dispose()


def legacy_rows(engine, tables):
    with engine.connect() as connection:
        return {name: sorted([tuple(r) for r in connection.execute(Base.metadata.tables[name].select())],
                             key=repr) for name in tables}


def test_v3_upgrade_is_explicit_additive_private_and_idempotent(legacy):
    _, engine, factory, old_tables = legacy
    before = legacy_rows(engine, old_tables)
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    assert legacy_rows(engine, old_tables) == before
    with engine.connect() as connection:
        assert schema_status(connection)["current"] == 3
        assert not (set(inspect(connection).get_table_names()) & set(SCOPE_TABLES))
    assert initialize_database(engine, allow_upgrade=True)["current"] == MIGRATIONS[-1][0]
    assert legacy_rows(engine, old_tables) == before
    with factory() as db:
        repo = ScopeRepository(db)
        resources = {r.document_id or r.memory_id or r.conversation_id: r
                     for r in db.scalars(select(ScopedResource))}
        assert resources["personal"].scope == resources["private-memory"].scope == "personal"
        assert resources["old-chat"].scope == "unresolved"
        assert resources["old-chat"].classification == "review_required"
        assert resources["old-derived"].reason == "legacy_message_provenance"
        assert resources["bad-digest"].classification == "review_required"
        assert db.scalar(select(func.count()).select_from(ProjectReadGrant)) == 0
        personal = db.scalar(select(ResourceVersion).where(
            ResourceVersion.resource_id == resources["personal"].id))
        assert repo.can_read_version("owner", None, personal.id)
        assert not repo.can_read_version("owner", resources["project"].project_id, personal.id)
        for key in ("old-chat", "old-derived", "bad-digest"):
            assert db.scalar(select(ResourceVersion).where(
                ResourceVersion.resource_id == resources[key].id)) is None
        counts = {t: db.scalar(select(func.count()).select_from(Base.metadata.tables[t])) for t in SCOPE_TABLES}
    initialize_database(engine, allow_upgrade=True)
    with engine.begin() as connection:
        classify_legacy_resources(connection)
    with factory() as db:
        assert counts == {t: db.scalar(select(func.count()).select_from(Base.metadata.tables[t])) for t in SCOPE_TABLES}
    assert legacy_rows(engine, old_tables) == before


def test_failure_after_classification_rolls_back_tables_rows_and_version(legacy, monkeypatch):
    _, engine, _, old_tables = legacy
    from role_ai import migrations

    original = migrations.validate_scope_guards
    before = legacy_rows(engine, old_tables)

    def fail_after_backfill(connection):
        original(connection)
        if connection.scalar(text("SELECT count(*) FROM scoped_resources")) == 6:
            raise ValueError("synthetic injected failure")

    monkeypatch.setattr(migrations, "validate_scope_guards", fail_after_backfill)
    with pytest.raises(MigrationRequired):
        initialize_database(engine, allow_upgrade=True)
    with engine.connect() as connection:
        assert schema_status(connection)["current"] == 3
        assert not (set(SCOPE_TABLES) & set(inspect(connection).get_table_names()))
        assert not list(connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'"))
    assert legacy_rows(engine, old_tables) == before
    monkeypatch.setattr(migrations, "validate_scope_guards", original)
    assert initialize_database(engine, allow_upgrade=True)["current"] == MIGRATIONS[-1][0]


@pytest.mark.parametrize("damage", ["trigger", "changed_trigger", "extra_trigger", "check", "index", "index_predicate"])
def test_scope_integrity_drift_is_rejected_without_repair(legacy, damage):
    _, engine, _, old_tables = legacy
    initialize_database(engine, allow_upgrade=True)
    before = legacy_rows(engine, old_tables)
    with engine.begin() as connection:
        if damage in {"trigger", "changed_trigger"}:
            connection.exec_driver_sql("DROP TRIGGER scope_grant_insert")
            if damage == "changed_trigger":
                connection.exec_driver_sql("CREATE TRIGGER scope_grant_insert BEFORE INSERT ON scope_read_grants BEGIN SELECT 1; END")
        elif damage == "extra_trigger":
            connection.exec_driver_sql("CREATE TRIGGER unexpected_scope_trigger BEFORE INSERT ON scoped_resources BEGIN SELECT 1; END")
        elif damage in {"index", "index_predicate"}:
            connection.exec_driver_sql("DROP INDEX uq_scope_active_grant")
            if damage == "index_predicate":
                connection.exec_driver_sql("CREATE UNIQUE INDEX uq_scope_active_grant ON scope_read_grants(version_id,project_id) WHERE revoked_at IS NOT NULL")
        else:
            connection.exec_driver_sql("PRAGMA writable_schema=ON")
            connection.exec_driver_sql("UPDATE sqlite_master SET sql=replace(sql, 'generation >= 1', 'generation >= 0') WHERE name='scope_policies'")
            connection.exec_driver_sql("PRAGMA writable_schema=OFF")
            connection.exec_driver_sql("PRAGMA schema_version=999")
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    assert legacy_rows(engine, old_tables) == before


def test_copy_preparation_reuses_backup_and_never_upgrades_source(legacy, tmp_path):
    settings, engine, _, old_tables = legacy
    before = legacy_rows(engine, old_tables)
    checkpoint = (Path(settings.coding_root) / "checkpoint.json").read_bytes()
    destination = tmp_path / "stage"
    with pytest.raises(ValueError):
        prepare_scope_copy(settings, destination)
    assert not destination.exists()
    report = prepare_scope_copy(settings, destination, offline=True)
    assert report["source_schema_version"] == 3 and report["candidate_schema_version"] == MIGRATIONS[-1][0]
    assert report["activation"] == "not_activated" and not report["coding_checkpoints_included"]
    verify_backup(report["backup"])
    assert (Path(report["candidate"]) / "artifacts/synthetic.txt").read_text() == "artifact preserved"
    with engine.connect() as connection:
        assert schema_status(connection)["current"] == 3
    assert legacy_rows(engine, old_tables) == before
    assert (Path(settings.coding_root) / "checkpoint.json").read_bytes() == checkpoint
    restored = tmp_path / "recovery"
    assert restore_backup(report["backup"], restored)["activation"] == "not_activated"
    restored_engine, _ = build_database(f"sqlite:///{restored / 'database.sqlite'}")
    try:
        assert legacy_rows(restored_engine, old_tables) == before
        with restored_engine.connect() as connection:
            assert schema_status(connection)["current"] == 3
    finally:
        restored_engine.dispose()
    with pytest.raises(ValueError):
        prepare_scope_copy(settings, destination, offline=True)


def test_failed_copy_keeps_verified_source_backup_and_original_data(legacy, tmp_path, monkeypatch):
    settings, engine, _, old_tables = legacy
    from role_ai import scope_migration

    before = legacy_rows(engine, old_tables)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic candidate failure")

    monkeypatch.setattr(scope_migration, "initialize_database", fail)
    with pytest.raises(RuntimeError):
        prepare_scope_copy(settings, tmp_path / "failed-stage", offline=True)
    verify_backup(tmp_path / "failed-stage/source-backup")
    assert legacy_rows(engine, old_tables) == before
    with engine.connect() as connection:
        assert schema_status(connection)["current"] == 3


def test_scope_copy_rejects_symlinks_existing_or_overlapping_destinations(legacy, tmp_path):
    settings, _, _, _ = legacy
    alias = tmp_path / "linked"
    alias.symlink_to(tmp_path, target_is_directory=True)
    for target in (Path(settings.storage_dir) / "stage", Path(settings.coding_root) / "stage",
                   tmp_path, alias / "stage"):
        with pytest.raises(ValueError):
            prepare_scope_copy(settings, target, offline=True)


def test_precreated_scope_tables_do_not_authorize_automatic_v3_backfill(legacy):
    _, engine, _, old_tables = legacy
    before = legacy_rows(engine, old_tables)
    with engine.begin() as connection:
        Base.metadata.create_all(connection, tables=[Base.metadata.tables[n] for n in SCOPE_TABLES])
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    with engine.connect() as connection:
        assert schema_status(connection)["current"] == 3
        assert connection.scalar(text("SELECT count(*) FROM scoped_resources")) == 0
    assert legacy_rows(engine, old_tables) == before


def test_classification_replay_never_revives_revoked_sharing(legacy):
    _, engine, factory, _ = legacy
    initialize_database(engine, allow_upgrade=True)
    with factory() as db:
        repo = ScopeRepository(db)
        private = db.scalar(select(ScopedResource).where(ScopedResource.document_id == "personal"))
        project = db.scalar(select(ScopedResource).where(ScopedResource.document_id == "project"))
        version = repo.record_current_version("owner", private.id)
        grant = repo.grant_read("owner", version.id, project.project_id)
        repo.revoke_read("owner", grant.id)
        grant_id, version_id, project_id = grant.id, version.id, project.project_id
        db.commit()
    with engine.begin() as connection:
        classify_legacy_resources(connection)
    with factory() as db:
        assert db.get(ProjectReadGrant, grant_id).revoked_at is not None
        assert not ScopeRepository(db).can_read_version("owner", project_id, version_id)


def test_v4_copy_and_restore_preserve_revoked_grants_and_provenance(legacy, tmp_path):
    from role_ai.models import MemoryProvenance

    settings, engine, factory, old_tables = legacy
    initialize_database(engine, allow_upgrade=True)
    with factory() as db:
        repo = ScopeRepository(db)
        private = db.scalar(select(ScopedResource).where(ScopedResource.document_id == 'personal'))
        source = repo.record_current_version('owner', private.id)
        db.add(Memory(id='new-derived', user_id='owner', project='A', kind='knowledge',
                      content='synthetic derived', state='confirmed', revision=1))
        db.flush()
        derived_resource = repo.register_source('owner', 'memory', 'new-derived')
        derived = repo.record_current_version('owner', derived_resource.id)
        grant = repo.grant_read('owner', source.id, derived_resource.project_id)
        edge = repo.record_provenance('owner', derived.id, source.id, grant.id)
        repo.revoke_read('owner', grant.id)
        grant_id, edge_id, version_id = grant.id, edge.id, derived.id
        project_id = derived_resource.project_id
        db.commit()
    tables = [*old_tables, *SCOPE_TABLES]
    before = legacy_rows(engine, tables)
    report = prepare_scope_copy(settings, tmp_path / 'v4-stage', offline=True)
    assert report['source_schema_version'] == report['candidate_schema_version'] == MIGRATIONS[-1][0]
    assert report['activation'] == 'not_activated'
    verify_backup(report['backup'])
    restored = tmp_path / 'v4-restored'
    restore_backup(report['backup'], restored)
    for path in [Path(report['candidate']) / 'database.sqlite', restored / 'database.sqlite']:
        copy_engine, copy_factory = build_database(f'sqlite:///{path}')
        try:
            assert legacy_rows(copy_engine, tables) == before
            with copy_factory() as db:
                assert db.get(ProjectReadGrant, grant_id).revoked_at is not None
                assert db.get(MemoryProvenance, edge_id) is not None
                assert db.get(Memory, 'new-derived').content == 'synthetic derived'
                assert not ScopeRepository(db).can_read_version('owner', project_id, version_id)
        finally:
            copy_engine.dispose()
    assert legacy_rows(engine, tables) == before


def test_v3_classification_isolated_for_two_owners_with_same_alias(legacy):
    _, engine, factory, old_tables = legacy
    with factory() as db:
        db.add(User(id='second', email='second@test.invalid', password_hash='synthetic'))
        db.flush()
        for doc_id, alias in [('second-project', 'A'), ('second-private', '')]:
            db.add(Document(id=doc_id, user_id='second', filename=doc_id + '.txt',
                            content=doc_id, digest=hashlib.sha256(doc_id.encode()).hexdigest(),
                            project=alias))
        db.commit()
    before = legacy_rows(engine, old_tables)
    initialize_database(engine, allow_upgrade=True)
    assert legacy_rows(engine, old_tables) == before
    with factory() as db:
        repo = ScopeRepository(db)
        first = db.scalar(select(ScopedResource).where(ScopedResource.document_id == 'project'))
        second = db.scalar(select(ScopedResource).where(ScopedResource.document_id == 'second-project'))
        private = db.scalar(select(ScopedResource).where(ScopedResource.document_id == 'second-private'))
        assert first.project_id != second.project_id
        assert private.scope == 'personal' and private.project_id is None
        version = repo.record_current_version('owner', first.id)
        assert not repo.can_read_version('second', second.project_id, version.id)
        assert db.scalar(select(func.count()).select_from(ProjectReadGrant)) == 0


@pytest.mark.parametrize('kind', ['document', 'memory', 'conversation'])
def test_populated_unversioned_complete_schema_requires_explicit_upgrade(tmp_path, kind):
    engine, factory = build_database(f"sqlite:///{tmp_path / 'unversioned.db'}")
    try:
        Base.metadata.create_all(engine)
        with factory() as db:
            db.add(User(id='owner', email='owner@test.invalid', password_hash='synthetic'))
            db.flush()
            sources = {
                'document': Document(id='source', user_id='owner', filename='synthetic.txt',
                                     content='synthetic', project='',
                                     digest=hashlib.sha256(b'synthetic').hexdigest()),
                'memory': Memory(id='source', user_id='owner', content='synthetic', kind='user',
                                 project='', revision=1, state='confirmed'),
                'conversation': Conversation(id='source', user_id='owner', title='synthetic'),
            }
            db.add(sources[kind])
            db.commit()
        old_tables = [name for version, names in MIGRATIONS if version <= 3 for name in names]
        before = legacy_rows(engine, old_tables)
        with pytest.raises(MigrationRequired):
            initialize_database(engine)
        with engine.connect() as connection:
            assert schema_status(connection)['current'] == 0
            assert 'schema_migrations' not in inspect(connection).get_table_names()
            assert connection.scalar(text('SELECT count(*) FROM scoped_resources')) == 0
        assert legacy_rows(engine, old_tables) == before
        assert initialize_database(engine, allow_upgrade=True)['current'] == MIGRATIONS[-1][0]
        assert legacy_rows(engine, old_tables) == before
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(ScopedResource)) == 1
            assert db.scalar(select(func.count()).select_from(ProjectReadGrant)) == 0
    finally:
        engine.dispose()
