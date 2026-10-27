import json
import subprocess

import pytest

from role_ai.baseline_manifest import SOURCE_FILES, collect_manifest, main
from role_ai.config import Settings


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for name in SOURCE_FILES:
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("private-source-contents")
    (root / ".env").write_text("API_TOKEN=private-env-contents")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "role_ai"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return root


def test_manifest_is_hashes_only_and_distinguishes_runtime_and_performance(checkout):
    settings = Settings(_env_file=None, openai_api_key="private-key-contents")
    report = collect_manifest(checkout, settings)
    text = json.dumps(report)
    assert "private-source-contents" not in text
    assert "private-env-contents" not in text
    assert "private-key-contents" not in text
    assert "API_TOKEN" not in text
    assert set(report["source_sha256"]) == set(SOURCE_FILES)
    assert report["performance"] == {"state": "not_measured", "actual_model_calls": 0}
    assert report["scope"] == "local_checkout_not_verified_runtime"
    assert len(report["commit"]) == 40
    assert report["dirty"]  # untracked .env is not read or included
    same = collect_manifest(checkout, Settings(_env_file=None, openai_api_key="another-key"))
    assert same["nonsecret_settings_sha256"] == report["nonsecret_settings_sha256"]


def test_source_change_changes_identity(checkout):
    settings = Settings(_env_file=None)
    before = collect_manifest(checkout, settings)
    (checkout / SOURCE_FILES[0]).write_text("different content")
    after = collect_manifest(checkout, settings)
    assert before["source_sha256"] != after["source_sha256"]


def test_source_symlink_rejected(checkout, tmp_path):
    source = checkout / SOURCE_FILES[0]
    source.unlink()
    secret = tmp_path / "secret"
    secret.write_text("secret-not-read")
    source.symlink_to(secret)
    with pytest.raises(ValueError, match="symlink"):
        collect_manifest(checkout, Settings(_env_file=None))


def test_cli_never_overwrites_existing_report(checkout, tmp_path, monkeypatch):
    target = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv", ["baseline", "--checkout", str(checkout), "--output", str(target)]
    )
    main()
    before = target.read_bytes()
    with pytest.raises(SystemExit) as exit_code:
        main()
    assert exit_code.value.code == 2
    assert target.read_bytes() == before
    assert target.stat().st_mode & 0o777 == 0o600
