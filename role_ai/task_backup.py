"""Offline companion for existing v1 DB/artifact backup plus common task metadata.

Restore is always to a new inactive directory; never changes live settings or replays work.
"""

import json
import re
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path

from role_ai.backup import BackupError, create_backup, inventory, restore_backup, verify_backup
from role_ai.coding_jobs import CodingJobError
from role_ai.task_checkpoints import TaskCheckpointStore
from role_ai.task_contracts import TaskContract

FORMAT = "role-ai-task-backup-v1"
LOCKS = {"task.lock", "execute.lock", "runtime.lock", "control.lock"}
FILES = {"checkpoint.json", "runtime.json", "control.json"}


def common_inventory(root):
    """No symlinks/special files, unknown files, orphan ledgers or retargeted namespaces."""
    found = inventory(root)
    files = {}
    for name, meta in found.items():
        parts = Path(name).parts
        if name == "create.lock":
            continue
        if (
            len(parts) != 3
            or not re.fullmatch("[0-9a-f]{64}", parts[0])
            or not re.fullmatch("[0-9a-f]{32}", parts[1])
        ):
            raise BackupError("Invalid task backup namespace")
        if parts[-1] in LOCKS:
            continue
        if parts[-1] not in FILES:
            raise BackupError("Unknown task metadata file")
        if not (root / parts[0] / parts[1] / "checkpoint.json").is_file():
            raise BackupError("Orphan task metadata")
        files[name] = meta
    return files


def validate_common(root):
    if not root.is_dir() or root.resolve() != root:
        raise BackupError("Invalid common task root")
    selected = common_inventory(root)
    store = TaskCheckpointStore(root)
    contracts = []
    try:
        for name in selected:
            if Path(name).name != "checkpoint.json":
                continue
            path = root / name
            payload = store.files.read(path)
            contract = TaskContract.model_validate_json(json.dumps(payload["contract"]))
            if store.directory(contract.user_id, contract.task_id) != path.parent:
                raise ValueError()
            value = store.load(contract.user_id, contract.task_id)
            runtime = path.parent / "runtime.json"
            if runtime.exists():
                ledger = store.files.read(runtime)
                if (
                    ledger.get("format") != 1
                    or ledger.get("contract_digest") != value["state"].contract_digest
                ):
                    raise ValueError()
                # Runtime profiles are not reconstituted or silently widened here.
                # Full policy/counter checks remain mandatory in TaskRuntime before use.
            control = path.parent / "control.json"
            if control.exists():
                action = store.files.read(control)
                if set(action) != {"action"} or action["action"] not in {"pause", "cancel"}:
                    raise ValueError()
            contracts.append(contract)
    except (CodingJobError, ValueError, KeyError, TypeError, AttributeError):
        raise BackupError("Task checkpoint integrity check failed; no replay") from None
    return selected, contracts


def verify_task_backup(directory):
    root = Path(directory).absolute()
    if root.resolve() != root or not root.is_dir():
        raise BackupError("Invalid task backup root")
    found = inventory(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 16777216:
        raise BackupError("Missing task backup manifest")
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (ValueError, OSError):
        raise BackupError("Invalid task backup manifest") from None
    actual = {name: meta for name, meta in found.items() if name != "manifest.json"}
    if (
        type(manifest) is not dict
        or set(manifest) != {"format", "files", "checkpoint_count"}
        or manifest["format"] != FORMAT
        or manifest["files"] != actual
        or type(manifest["checkpoint_count"]) is not int
        or any(not name.startswith(("application/", "task-checkpoints/")) for name in actual)
    ):
        raise BackupError("Task backup inventory mismatch")
    verify_backup(root / "application")
    _, contracts = validate_common(root / "task-checkpoints")
    if len(contracts) != manifest["checkpoint_count"]:
        raise BackupError("Task checkpoint count mismatch")
    return manifest


def create_task_backup(settings, destination, *, offline=False):
    if not offline:
        raise BackupError("Stop all APIs/workers and explicitly confirm offline first")
    target = Path(destination).absolute()
    source = Path(settings.coding_root).absolute() / "contracts"
    artifacts = Path(settings.storage_dir).absolute()
    if target.exists() or target.is_symlink() or target.parent.resolve() != target.parent:
        raise BackupError("New canonical backup destination required")
    if any(target.is_relative_to(p) or p.is_relative_to(target) for p in (source, artifacts)):
        raise BackupError("Backup overlaps source")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".task-backup-", dir=target.parent))
    temporary.chmod(0o700)
    try:
        with ExitStack() as stack:
            contracts = []
            if source.exists() or source.is_symlink():
                store = TaskCheckpointStore(source)
                stack.enter_context(store.files.lock(source, "create"))
                _, contracts = validate_common(source)
                for contract in sorted(contracts, key=lambda c: (c.user_id, c.task_id)):
                    directory = store.directory(contract.user_id, contract.task_id)
                    for name in ("execute", "runtime", "control", "task"):
                        stack.enter_context(store.files.lock(directory, name))
                before, _ = validate_common(source)
            else:
                before = {}
            # Original v1 structure/restore remains unchanged.
            create_backup(settings, temporary / "application", offline=True)
            copied = temporary / "task-checkpoints"
            copied.mkdir(mode=0o700)
            for relative in before:
                dst = copied / relative
                dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source / relative, dst)
                dst.chmod(0o600)
            if source.exists() and before != validate_common(source)[0]:
                raise BackupError("Task metadata changed during offline backup")
            if before != validate_common(copied)[0]:
                raise BackupError("Task metadata copy mismatch")
            manifest = {
                "format": FORMAT,
                "files": inventory(temporary),
                "checkpoint_count": len(contracts),
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (temporary / "manifest.json").chmod(0o600)
            verify_task_backup(temporary)
            target.mkdir(mode=0o700)
            try:
                for entry in temporary.iterdir():
                    entry.rename(target / entry.name)
            except BaseException:
                shutil.rmtree(target)
                raise
        return {
            "state": "created",
            "format": FORMAT,
            "checkpoint_count": len(contracts),
            "activation": "not_activated",
        }
    except CodingJobError:
        raise BackupError("Task writer active or checkpoint invalid; stop all workers") from None
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def restore_task_backup(directory, destination):
    root = Path(directory).absolute()
    verify_task_backup(root)
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink() or target.parent.resolve() != target.parent:
        raise BackupError("Restore requires a new canonical destination")
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise BackupError("Backup/restore overlap")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    try:
        restore_backup(root / "application", target / "application")
        shutil.copytree(root / "task-checkpoints", target / "task-checkpoints")
        shutil.copyfile(root / "manifest.json", target / "manifest.json")
        for path in target.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        verify_task_backup(target)
    except BaseException:
        shutil.rmtree(target)
        raise
    return {
        "state": "restored",
        "activation": "not_activated",
        "format": FORMAT,
        "task_root": "task-checkpoints",
        "application_root": "application",
        "automatic_replay": False,
    }


def main():
    import argparse
    import sqlite3
    from role_ai.config import Settings

    parser = argparse.ArgumentParser(
        description="오프라인 DB/artifacts+공통 작업 기록 백업; 새 경로 비활성 복원"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create")
    create.add_argument("destination")
    create.add_argument("--offline", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("backup")
    restore = commands.add_parser("restore")
    restore.add_argument("backup")
    restore.add_argument("destination")
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = create_task_backup(Settings(), args.destination, offline=args.offline)
        elif args.action == "verify":
            manifest = verify_task_backup(args.backup)
            result = {
                "state": "verified",
                "format": FORMAT,
                "checkpoint_count": manifest["checkpoint_count"],
            }
        else:
            result = restore_task_backup(args.backup, args.destination)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(
            2,
            "백업/복원 실패: 오프라인·경로·기록 무결성을 확인하세요. 자동 활성화/재실행하지 않습니다.\n",
        )


if __name__ == "__main__":
    main()
