import pytest
from fastapi.testclient import TestClient

from role_ai.config import Settings
from role_ai.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}",
        registration_enabled=True, demo_enabled=True, default_provider="mock",
        allowed_origins=["http://testserver"],
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def signed_in(client):
    response = client.post("/api/auth/register", json={
        "email": "alice@example.test", "password": "safe-test-password-123"
    })
    assert response.status_code == 201
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
