import json
import sqlite3
from contextlib import closing

import pytest

from role_ai.artifacts import storage_lock
from role_ai.backup import BackupError, create_backup, restore_backup, verify_backup
from role_ai.config import Settings


@pytest.fixture
def source(tmp_path):
    db = tmp_path / 'live.db'
    with closing(sqlite3.connect(db)) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY, text TEXT)')
        connection.execute('INSERT INTO sample VALUES(1, ?)', ('개인 데이터',))
        connection.commit()
    root = tmp_path / 'artifacts'
    root.mkdir()
    (root / 'sample.jsonl').write_text('승인된 데이터', encoding='utf-8')
    return Settings(_env_file=None, database_url=f'sqlite:///{db}', storage_dir=str(root))


def test_backup_round_trip_and_no_overwrite(source, tmp_path):
    backup, restored = tmp_path / 'backup', tmp_path / 'restored'
    assert create_backup(source, backup, offline=True)['state'] == 'created'
    verify_backup(backup)
    verify_backup(backup)
    assert restore_backup(backup, restored)['activation'] == 'not_activated'
    assert (restored / 'artifacts/sample.jsonl').read_text() == '승인된 데이터'
    with closing(sqlite3.connect(restored / 'database.sqlite')) as db:
        assert db.execute('SELECT text FROM sample').fetchone()[0] == '개인 데이터'
    for operation in (lambda: create_backup(source, backup, offline=True),
                      lambda: restore_backup(backup, restored),
                      lambda: create_backup(source, tmp_path / 'online')):
        with pytest.raises(BackupError):
            operation()


@pytest.mark.parametrize('damage', ['modified', 'missing', 'extra', 'symlink', 'escape'])
def test_backup_rejects_corruption(source, tmp_path, damage):
    backup = tmp_path / 'backup'
    create_backup(source, backup, offline=True)
    path = backup / 'artifacts/sample.jsonl'
    if damage == 'modified':
        path.write_text('changed')
    elif damage == 'missing':
        path.unlink()
    elif damage == 'extra':
        (backup / 'extra').write_text('extra')
    elif damage == 'symlink':
        path.unlink()
        path.symlink_to(tmp_path / 'live.db')
    else:
        manifest = json.loads((backup / 'manifest.json').read_text())
        manifest['files']['../escape'] = {'bytes': 1, 'sha256': 'x'}
        (backup / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(BackupError):
        restore_backup(backup, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_backup_excludes_secrets_and_refuses_active_storage(source, tmp_path):
    (tmp_path / '.env').write_text('API_SECRET=must-not-copy')
    root = tmp_path / 'artifacts'
    with storage_lock(root):
        with pytest.raises(ValueError):
            create_backup(source, tmp_path / 'busy', offline=True)
    assert not (tmp_path / 'busy').exists()
    create_backup(source, tmp_path / 'backup', offline=True)
    assert not (tmp_path / 'backup/.env').exists()
    (root / 'link').symlink_to(tmp_path / '.env')
    with pytest.raises(BackupError):
        create_backup(source, tmp_path / 'unsafe', offline=True)
