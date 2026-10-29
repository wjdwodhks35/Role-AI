"""Offline, explicit copy preparation. Never upgrade or activate the source DB.

This is an internal foundation, with no CLI/HTTP route. The caller must obtain
separate approval for real data, stop all writers, and supply a new destination.
Existing Backup handles DB + artifacts only; Coding checkpoints are not included.
"""

import json
from pathlib import Path

from role_ai.backup import create_backup, restore_backup, sqlite_path
from role_ai.database import build_database
from role_ai.migrations import initialize_database, schema_status


def prepare_scope_copy(settings, destination, *, offline=False):
    if not offline:
        raise ValueError("Scope copy preparation requires explicit offline confirmation")
    source = sqlite_path(settings)
    root = Path(destination).absolute()
    artifacts = Path(settings.storage_dir).absolute()
    coding = Path(settings.coding_root).absolute()
    if (not source.is_file() or root.resolve() != root or root.exists()
            or root.is_symlink() or root.parent.resolve() != root.parent):
        raise ValueError("Scope staging requires a new canonical destination")
    for protected in (artifacts, coding):
        canonical = protected.resolve()
        if root.is_relative_to(canonical) or canonical.is_relative_to(root):
            raise ValueError("Scope staging overlaps protected data")
    if source.is_relative_to(root):
        raise ValueError("Scope staging overlaps the source database")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    backup = root / "source-backup"
    candidate = root / "candidate"
    create_backup(settings, backup, offline=True)
    restore_backup(backup, candidate)
    # The candidate is a staging DB, not an unchanged backup bundle after migration.
    (candidate / "manifest.json").rename(candidate / "source-backup-manifest.json")
    staged = settings.model_copy(update={
        "database_url": f"sqlite:///{candidate / 'database.sqlite'}",
        "storage_dir": str(candidate / "artifacts"),
    })
    engine, _ = build_database(staged.database_url)
    try:
        with engine.connect() as connection:
            before = schema_status(connection)["current"]
        result = initialize_database(engine, allow_upgrade=True)
    finally:
        engine.dispose()
    report = {"source_schema_version": before, "candidate_schema_version": result["current"],
              "activation": "not_activated", "source_db_upgraded": False,
              "coding_checkpoints_included": False, "backup": str(backup),
              "candidate": str(candidate)}
    (candidate / "staging.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    (candidate / "staging.json").chmod(0o600)
    return report
