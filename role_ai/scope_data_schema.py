"""v5 additive, immutable archive and new-conversation metadata; no legacy backfill."""

import re
from sqlalchemy import CheckConstraint, inspect
from role_ai.database import Base

DATA_SCOPE_TABLES = ("scope_archives", "scope_conversation_bindings", "scope_conversation_sources")


def statements():
    result = {}
    bodies = {
        "scope_archives": """SELECT RAISE(ABORT,'archive owner mismatch') WHERE NOT EXISTS
          (SELECT 1 FROM scoped_resources r WHERE r.id=NEW.resource_id AND r.user_id=NEW.user_id);""",
        "scope_conversation_bindings": """SELECT RAISE(ABORT,'conversation binding mismatch') WHERE NOT EXISTS
          (SELECT 1 FROM conversations c JOIN scope_policies s ON s.user_id=c.user_id
           WHERE c.id=NEW.conversation_id AND c.user_id=NEW.user_id AND s.generation=NEW.generation
           AND (NEW.project_id IS NULL OR EXISTS(SELECT 1 FROM scope_projects p
            WHERE p.id=NEW.project_id AND p.user_id=c.user_id AND p.state='active')));""",
        "scope_conversation_sources": """SELECT RAISE(ABORT,'conversation source mismatch') WHERE NOT EXISTS
          (SELECT 1 FROM scope_conversation_bindings b JOIN scope_resource_versions v
           ON v.id=NEW.version_id JOIN scoped_resources r ON r.id=v.resource_id
           WHERE b.conversation_id=NEW.conversation_id AND r.user_id=b.user_id AND
           ((NEW.grant_id IS NULL AND r.project_id IS b.project_id AND r.scope!='unresolved') OR
            EXISTS(SELECT 1 FROM scope_read_grants g WHERE g.id=NEW.grant_id AND g.version_id=v.id
             AND g.project_id=b.project_id AND g.granted_by=b.user_id AND g.revoked_at IS NULL)));""",
    }
    for table, body in bodies.items():
        name = table + "_insert"
        result[name] = f"CREATE TRIGGER {name} BEFORE INSERT ON {table} BEGIN {body} END"
        for event in ("UPDATE", "DELETE"):
            name = table + "_no_" + event.lower()
            result[name] = (
                f"CREATE TRIGGER {name} BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'scope record is immutable'); END"
            )
    name = "scope_archive_generation"
    result[name] = f"""CREATE TRIGGER {name} AFTER INSERT ON scope_archives BEGIN
        INSERT INTO scope_policies(user_id,generation) VALUES(NEW.user_id,2)
        ON CONFLICT(user_id) DO UPDATE SET generation=generation+1; END"""
    return result


def install_data_guards(connection):
    names = set(
        connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'").scalars()
    )
    for name, statement in statements().items():
        if name not in names:
            connection.exec_driver_sql(statement)


def validate_data_guards(connection):
    def canonical(value):
        return re.sub(r"\s+", " ", value).strip().rstrip(";")

    rows = list(
        connection.exec_driver_sql(
            "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'"
        )
    )
    found = {name: sql for name, _, sql in rows}
    expected = statements()
    if any(table in DATA_SCOPE_TABLES and name not in expected for name, table, _ in rows):
        raise ValueError("Unexpected data scope trigger")
    if any(
        name not in found or canonical(found[name]) != canonical(sql)
        for name, sql in expected.items()
    ):
        raise ValueError("Data scope integrity drift")
    inspector = inspect(connection)
    for name in DATA_SCOPE_TABLES:
        checks = {c["name"]: canonical(c["sqltext"]) for c in inspector.get_check_constraints(name)}
        target = {
            c.name: canonical(str(c.sqltext))
            for c in Base.metadata.tables[name].constraints
            if isinstance(c, CheckConstraint)
        }
        if checks != target:
            raise ValueError("Data scope constraint drift")
