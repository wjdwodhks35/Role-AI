"""Additive approval tombstones, immutable payload with revoke-only updates."""

import re
from sqlalchemy import inspect, CheckConstraint
from role_ai.database import Base

APPROVAL_TABLES = ("task_approvals",)


def statements():
    return {
        "task_approval_no_delete": "CREATE TRIGGER task_approval_no_delete BEFORE DELETE ON task_approvals BEGIN SELECT RAISE(ABORT,'approval history retained'); END",
        "task_approval_revoke_only": """CREATE TRIGGER task_approval_revoke_only BEFORE UPDATE ON task_approvals BEGIN
          SELECT RAISE(ABORT,'approval is immutable except revocation') WHERE
          NEW.id!=OLD.id OR NEW.user_id!=OLD.user_id OR NEW.session_hash!=OLD.session_hash OR
          NEW.task_id!=OLD.task_id OR NEW.contract_digest!=OLD.contract_digest OR
          NEW.contract_json!=OLD.contract_json OR NEW.environment_digest!=OLD.environment_digest OR
          NEW.created_at!=OLD.created_at OR NEW.expires_at!=OLD.expires_at OR
          OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL; END""",
    }


def install_approval_guards(connection):
    names = set(
        connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'").scalars()
    )
    for name, sql in statements().items():
        if name not in names:
            connection.exec_driver_sql(sql)


def validate_approval_guards(connection):
    def canon(s):
        return re.sub(r"\s+", " ", s).strip().rstrip(";")

    expected = statements()
    found = {
        name: sql
        for name, sql in connection.exec_driver_sql(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='task_approvals'"
        )
    }
    if set(expected) != set(found) or any(canon(found[k]) != canon(v) for k, v in expected.items()):
        raise ValueError("Approval integrity drift")
    checks = {
        x["name"]: canon(x["sqltext"])
        for x in inspect(connection).get_check_constraints("task_approvals")
    }
    target = {
        c.name: canon(str(c.sqltext))
        for c in Base.metadata.tables["task_approvals"].constraints
        if isinstance(c, CheckConstraint)
    }
    if checks != target:
        raise ValueError("Approval constraint drift")
