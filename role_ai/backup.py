"""Offline SQLite + artifact backup. Restore only into a new destination, never overwrite."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import stat
import tempfile
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.engine import make_url

from role_ai.artifacts import storage_lock
from role_ai.config import Settings


class BackupError(ValueError):
    pass


def sqlite_path(settings):
    url = make_url(settings.database_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:" or url.query:
        raise BackupError("파일 기반 SQLite DB만 지원합니다.")
    path = Path(url.database).absolute()
    if path.is_symlink() or path.resolve() != path:
        raise BackupError("DB의 심볼릭 링크 경로는 지원하지 않습니다.")
    return path


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1048576), b""):
            h.update(part)
    return h.hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise BackupError("심볼릭 링크·특수 파일은 백업/복원할 수 없습니다.")
        if path.is_file() and path.name not in {".write-lock", ".collection-lock"}:
            result[path.relative_to(root).as_posix()] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return result


@contextmanager
def exclusive_collection(root):
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".collection-lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("수집 Worker가 실행 중입니다. 모든 서비스를 먼저 중지하세요.") from None
        yield


def create_backup(settings, destination, *, offline=False):
    if not offline:
        raise BackupError("API와 모든 Worker 중지 확인 후 --offline이 필요합니다.")
    source = sqlite_path(settings)
    artifacts = Path(settings.storage_dir).absolute()
    target = Path(destination).absolute()
    if not source.is_file() or artifacts.is_symlink() or artifacts.resolve() != artifacts:
        raise BackupError("DB·artifact 경로를 확인하세요.")
    if target.exists() or target.is_symlink() or target.parent.resolve() != target.parent:
        raise BackupError("백업 대상은 심볼릭 링크가 아닌 새 경로여야 합니다.")
    if target.is_relative_to(artifacts) or artifacts.is_relative_to(target):
        raise BackupError("백업과 artifact 경로가 겹칩니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(exclusive_collection(artifacts))
        stack.enter_context(storage_lock(artifacts))
        # Prevent other SQLite writers while taking the DB snapshot and copying artifacts.
        connection = stack.enter_context(closing(sqlite3.connect(source.as_uri() + "?mode=rw", uri=True, timeout=1)))
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            raise BackupError("DB 쓰기 작업이 진행 중입니다. 서비스를 중지하세요.") from None
        temporary = Path(tempfile.mkdtemp(prefix=".role-ai-backup-", dir=target.parent))
        temporary.chmod(0o700)
        try:
            before = inventory(artifacts)
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
                with closing(sqlite3.connect(temporary / "database.sqlite")) as output:
                    reader.backup(output)
                    output.execute("PRAGMA journal_mode=DELETE")
            (temporary / "database.sqlite").chmod(0o600)
            copied = temporary / "artifacts"
            copied.mkdir(mode=0o700)
            for relative in before:
                dst = copied / relative
                dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(artifacts / relative, dst)
                dst.chmod(0o600)
            if before != inventory(artifacts) or before != inventory(copied):
                raise BackupError("백업 중 파일 변경 감지. 서비스를 중지하고 다시 시도하세요.")
            files = inventory(temporary)
            manifest = {"format": "role-ai-backup-v1", "files": files,
                        "excluded": [".env", "외부 기반 모델", "외부 Docker volume", "가상환경"]}
            (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            (temporary / "manifest.json").chmod(0o600)
            verify_backup(temporary)
            # A target created concurrently must not be overwritten, even if empty.
            target.mkdir(mode=0o700)
            try:
                for entry in temporary.iterdir():
                    entry.rename(target / entry.name)
            except BaseException:
                shutil.rmtree(target)
                raise
            return {"state": "created", "file_count": len(files)}
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            connection.rollback()


def verify_backup(directory):
    root = Path(directory).absolute()
    if root.is_symlink() or root.resolve() != root or not root.is_dir():
        raise BackupError("잘못된 백업 경로입니다.")
    found = inventory(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 16777216:
        raise BackupError("백업 manifest가 없거나 너무 큽니다.")
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
        actual = {k: v for k, v in found.items() if k != "manifest.json"}
        if manifest["format"] != "role-ai-backup-v1" or manifest["files"] != actual:
            raise BackupError("백업 파일 목록·크기·해시가 일치하지 않습니다.")
        if "database.sqlite" not in actual or any(
            name != "database.sqlite" and not name.startswith("artifacts/") for name in actual
        ):
            raise BackupError("허용되지 않은 백업 파일입니다.")
        with closing(sqlite3.connect("file:" + quote(str(root / "database.sqlite")) + "?mode=ro&immutable=1", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("SQLite 무결성 검사 실패")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise BackupError("SQLite 외래키 검사 실패")
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, sqlite3.Error):
        raise BackupError("백업 구조 또는 DB 검증에 실패했습니다.") from None
    return manifest


def restore_backup(directory, destination):
    root = Path(directory).absolute()
    verify_backup(root)
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink() or target.parent.resolve() != target.parent:
        raise BackupError("복원은 기존 데이터가 없는 새 경로에만 가능합니다.")
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise BackupError("백업과 복원 경로가 겹칩니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Restore the complete verified bundle, including manifest, so it can be checked again.
    target.mkdir(mode=0o700)
    try:
        for path in (root / "manifest.json", root / "database.sqlite"):
            shutil.copyfile(path, target / path.name)
            (target / path.name).chmod(0o600)
        shutil.copytree(root / "artifacts", target / "artifacts")
        for path in (target / "artifacts").rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        (target / "artifacts").chmod(0o700)
        verify_backup(target)
    except BaseException:
        shutil.rmtree(target)
        raise
    return {"state": "restored", "activation": "not_activated"}


def main():
    parser = argparse.ArgumentParser(description="오프라인 백업·검증·새 경로 복원")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("destination")
    create.add_argument("--offline", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("backup")
    restore = sub.add_parser("restore")
    restore.add_argument("backup")
    restore.add_argument("destination")
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = create_backup(Settings(), args.destination, offline=args.offline)
        elif args.action == "verify":
            manifest = verify_backup(args.backup)
            result = {"state": "verified", "file_count": len(manifest["files"])}
        else:
            result = restore_backup(args.backup, args.destination)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(2, "백업/복원 실패. 서비스 중지·경로·여유 공간·파일 무결성을 확인하세요.\n")


if __name__ == "__main__":
    main()
