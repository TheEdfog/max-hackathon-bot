from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.db.database import get_db
from apps.web.dependencies import get_current_user, get_current_user_optional
from apps.web.main import create_app


class _DummyDb:
    def commit(self) -> None:
        return None

    def refresh(self, _obj=None) -> None:
        return None

    def add(self, _obj) -> None:
        return None

    def flush(self) -> None:
        return None

    def execute(self, *_args, **_kwargs):
        return None

    def scalar(self, *_args, **_kwargs):
        return None


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr("apps.web.main.init_db", lambda: None)
    application = create_app()

    def _fake_db():
        yield _DummyDb()

    application.dependency_overrides[get_db] = _fake_db
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(app):
    fake_user = SimpleNamespace(id=1, email="user@example.com")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_current_user_optional] = lambda: fake_user

    with TestClient(app) as test_client:
        yield test_client
