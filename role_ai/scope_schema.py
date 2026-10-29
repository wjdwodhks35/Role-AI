"""SQLite integrity guards for new scope tables only; no triggers on legacy tables."""

import re

from sqlalchemy import CheckConstraint, inspect

from role_ai.database import Base

SCOPE_TABLES = (
    "scope_projects", "scope_policies", "scoped_resources", "scope_resource_versions",
    "scope_read_grants", "scope_memory_provenance",
)


def _trigger(name, event, table, body):
    return f"CREATE TRIGGER {name} {event} ON {table} BEGIN {body} END"


def scope_triggers():
    result = {}
    owner_check = """
      SELECT RAISE(ABORT, 'scope resource ownership mismatch') WHERE NOT EXISTS (
        SELECT 1 FROM documents WHERE NEW.kind='document' AND id=NEW.document_id AND user_id=NEW.user_id
        UNION ALL SELECT 1 FROM memories WHERE NEW.kind='memory' AND id=NEW.memory_id AND user_id=NEW.user_id
        UNION ALL SELECT 1 FROM conversations WHERE NEW.kind='conversation' AND id=NEW.conversation_id AND user_id=NEW.user_id
      );
      SELECT RAISE(ABORT, 'scope project ownership mismatch')
      WHERE NEW.project_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM scope_projects WHERE id=NEW.project_id AND user_id=NEW.user_id
      );
    """
    for event in ("INSERT", "UPDATE"):
        name = "scope_resource_" + event.lower()
        result[name] = _trigger(name, "BEFORE " + event, "scoped_resources", owner_check)
    result["scope_resource_identity"] = _trigger(
        "scope_resource_identity", "BEFORE UPDATE", "scoped_resources", """
        SELECT RAISE(ABORT, 'scope resource identity is immutable') WHERE
          NEW.id != OLD.id OR NEW.user_id != OLD.user_id OR NEW.kind != OLD.kind OR
          NEW.document_id IS NOT OLD.document_id OR NEW.memory_id IS NOT OLD.memory_id OR
          NEW.conversation_id IS NOT OLD.conversation_id OR NEW.scope != OLD.scope OR
          NEW.project_id IS NOT OLD.project_id OR NEW.legacy_project != OLD.legacy_project;
        """)
    result["scope_project_owner"] = _trigger(
        "scope_project_owner", "BEFORE UPDATE", "scope_projects", """
        SELECT RAISE(ABORT, 'scope project identity is immutable')
        WHERE NEW.id != OLD.id OR NEW.user_id != OLD.user_id;
        """)
    result["scope_version_insert"] = _trigger(
        "scope_version_insert", "BEFORE INSERT", "scope_resource_versions", """
        SELECT RAISE(ABORT, 'invalid exact scope version') WHERE NOT EXISTS (
          SELECT 1 FROM scoped_resources r WHERE r.id=NEW.resource_id AND (
            (r.kind='document' AND length(NEW.version)=64 AND
             NEW.version NOT GLOB '*[^0-9a-f]*' AND NEW.version=NEW.content_digest) OR
            (r.kind='memory' AND length(NEW.version) BETWEEN 1 AND 19 AND
             substr(NEW.version,1,1) GLOB '[1-9]' AND NEW.version NOT GLOB '*[^0-9]*')
          )
        );
        """)
    result["scope_grant_insert"] = _trigger(
        "scope_grant_insert", "BEFORE INSERT", "scope_read_grants", """
        SELECT RAISE(ABORT, 'scope grant ownership or classification mismatch') WHERE NOT EXISTS (
          SELECT 1 FROM scope_resource_versions v JOIN scoped_resources r ON r.id=v.resource_id
          JOIN scope_projects p ON p.id=NEW.project_id
          WHERE v.id=NEW.version_id AND r.user_id=NEW.granted_by AND p.user_id=r.user_id
            AND p.state='active' AND r.classification='ready' AND r.scope!='unresolved'
        );
        """)
    result["scope_grant_revoke_only"] = _trigger(
        "scope_grant_revoke_only", "BEFORE UPDATE", "scope_read_grants", """
        SELECT RAISE(ABORT, 'scope grants can only be revoked') WHERE
          NEW.id != OLD.id OR NEW.version_id != OLD.version_id OR NEW.project_id != OLD.project_id OR
          NEW.granted_by != OLD.granted_by OR NEW.permission != OLD.permission OR
          NEW.created_at != OLD.created_at OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL;
        """)
    for event in ("INSERT", "UPDATE"):
        name = "scope_grant_generation_" + event.lower()
        result[name] = _trigger(name, "AFTER " + event, "scope_read_grants", """
          INSERT INTO scope_policies(user_id,generation) VALUES(NEW.granted_by,2)
          ON CONFLICT(user_id) DO UPDATE SET generation=generation+1;
        """)
    # Revoked grant references are valid historical dependencies, never read permission.
    result["scope_provenance_insert"] = _trigger(
        "scope_provenance_insert", "BEFORE INSERT", "scope_memory_provenance", """
        SELECT RAISE(ABORT, 'scope provenance mismatch') WHERE NOT EXISTS (
          SELECT 1 FROM scope_resource_versions dv JOIN scoped_resources dr ON dr.id=dv.resource_id
          JOIN scope_resource_versions sv ON sv.id=NEW.source_version_id
          JOIN scoped_resources sr ON sr.id=sv.resource_id
          WHERE dv.id=NEW.derived_version_id AND dr.kind='memory' AND dr.user_id=sr.user_id
            AND dr.classification='ready' AND sr.classification='ready'
            AND dr.scope!='unresolved' AND sr.scope!='unresolved'
            AND ((dr.scope=sr.scope AND dr.project_id IS sr.project_id AND NEW.grant_id IS NULL)
              OR EXISTS (SELECT 1 FROM scope_read_grants g WHERE g.id=NEW.grant_id
                AND g.version_id=sv.id AND g.project_id=dr.project_id
                AND g.granted_by=dr.user_id))
        );
        SELECT RAISE(ABORT, 'scope provenance cycle') WHERE EXISTS (
          WITH RECURSIVE ancestors(id) AS (
            SELECT NEW.source_version_id UNION
            SELECT p.source_version_id FROM scope_memory_provenance p JOIN ancestors a
              ON p.derived_version_id=a.id
          ) SELECT 1 FROM ancestors WHERE id=NEW.derived_version_id
        );
        """)
    for table in ("scope_resource_versions", "scope_memory_provenance"):
        for event in ("UPDATE", "DELETE"):
            name = table + "_no_" + event.lower()
            result[name] = _trigger(name, "BEFORE " + event, table,
                                    "SELECT RAISE(ABORT, 'scope history is immutable');")
    result["scope_grant_no_delete"] = _trigger(
        "scope_grant_no_delete", "BEFORE DELETE", "scope_read_grants",
        "SELECT RAISE(ABORT, 'scope grant history is retained');")
    return result


def install_scope_guards(connection):
    existing = set(connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='trigger'").scalars())
    for name, statement in scope_triggers().items():
        if name not in existing:
            connection.exec_driver_sql(statement)


def validate_scope_guards(connection):
    def canonical(value):
        return re.sub(r"\s+", " ", value).strip().rstrip(";")

    triggers = list(connection.exec_driver_sql(
        "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'"))
    found = {name: sql for name, _, sql in triggers}
    expected_triggers = scope_triggers()
    if any(table in SCOPE_TABLES and name not in expected_triggers for name, table, _ in triggers):
        raise ValueError("Unexpected scope integrity trigger")
    for name, expected in expected_triggers.items():
        if name not in found or canonical(found[name]) != canonical(expected):
            raise ValueError("Scope integrity trigger is missing or changed")
    inspector = inspect(connection)
    for name in SCOPE_TABLES:
        actual = {c["name"]: canonical(c["sqltext"]) for c in inspector.get_check_constraints(name)}
        expected = {c.name: canonical(str(c.sqltext)) for c in Base.metadata.tables[name].constraints
                    if isinstance(c, CheckConstraint)}
        if actual != expected:
            raise ValueError("Scope check constraints are missing or changed")
    indexes = inspector.get_indexes("scope_read_grants")
    active = next((i for i in indexes if i["name"] == "uq_scope_active_grant"), None)
    predicate = active.get("dialect_options", {}).get("sqlite_where") if active else None
    if predicate is None or canonical(str(predicate)) != "revoked_at IS NULL":
        raise ValueError("Scope active grant predicate is missing or changed")
