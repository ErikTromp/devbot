from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings, override_settings
from app.db.session import get_db
from app.main import create_app


@pytest.fixture
def locked_settings(settings: Settings) -> Settings:
    locked = settings.model_copy(update={"dashboard_username": "ops", "dashboard_password": "s3cret"})
    override_settings(locked)
    return locked


@pytest.fixture
def locked_client(locked_settings: Settings, session: Session) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_settings_dep() -> Settings:
        return locked_settings

    def override_db() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_settings] = override_settings_dep
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client


def test_blank_dashboard_credentials_are_rejected(settings: Settings) -> None:
    payload = settings.model_dump()
    payload.update(dashboard_username="  ", dashboard_password="")
    with pytest.raises(ValidationError):
        Settings(**payload)


def test_board_and_jobs_require_login(locked_client: TestClient) -> None:
    board = locked_client.get("/", follow_redirects=False)
    assert board.status_code == 303
    assert board.headers["location"].startswith("/login")

    jobs = locked_client.get("/jobs")
    assert jobs.status_code == 401

    health = locked_client.get("/health")
    assert health.status_code == 200


def test_login_rejects_bad_password(locked_client: TestClient) -> None:
    response = locked_client.post(
        "/login",
        data={"username": "ops", "password": "nope", "next": "/"},
    )
    assert response.status_code == 401
    assert "wrong" in response.text


def test_login_then_logout(locked_client: TestClient) -> None:
    denied = locked_client.get("/tickets/DEV-1", follow_redirects=False)
    assert denied.status_code == 303

    login = locked_client.post(
        "/login",
        data={"username": "ops", "password": "s3cret", "next": "/"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/"

    board = locked_client.get("/")
    assert board.status_code == 200
    assert "Sign out" in board.text
    assert locked_client.get("/jobs").status_code == 200

    logout = locked_client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert locked_client.get("/", follow_redirects=False).status_code == 303
