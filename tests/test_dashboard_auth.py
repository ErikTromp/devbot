from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

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


def _session_https_only(app) -> bool:
    layers = [layer for layer in app.user_middleware if layer.cls is SessionMiddleware]
    assert layers
    return layers[-1].kwargs["https_only"]


def test_public_base_url_marks_session_cookie_secure(settings: Settings) -> None:
    override_settings(settings.model_copy(update={"base_url": "https://devbot.example.com"}))
    assert _session_https_only(create_app()) is True
    override_settings(settings.model_copy(update={"base_url": "http://192.168.1.20:8100"}))
    assert _session_https_only(create_app()) is True


def test_localhost_base_url_keeps_session_cookie_on_http(settings: Settings) -> None:
    override_settings(settings.model_copy(update={"base_url": ""}))
    assert _session_https_only(create_app()) is False
    override_settings(settings.model_copy(update={"base_url": "http://127.0.0.1:8100"}))
    assert _session_https_only(create_app()) is False
    override_settings(settings.model_copy(update={"base_url": "http://localhost:8100"}))
    assert _session_https_only(create_app()) is False


def test_board_and_jobs_require_login(locked_client: TestClient) -> None:
    board = locked_client.get("/", follow_redirects=False)
    assert board.status_code == 303
    assert board.headers["location"].startswith("/login")

    jobs = locked_client.get("/jobs")
    assert jobs.status_code == 401

    health = locked_client.get("/health")
    assert health.status_code == 200

    created = locked_client.post("/jobs", json={"repository": "acme/web", "request": "hi"})
    assert created.status_code == 401

    docs = locked_client.get("/docs", follow_redirects=False)
    assert docs.status_code == 303
    assert docs.headers["location"].startswith("/login")
    redoc = locked_client.get("/redoc", follow_redirects=False)
    assert redoc.status_code == 303

    schema = locked_client.get("/openapi.json")
    assert schema.status_code == 401

    static = locked_client.get("/static/dashboard.css")
    assert static.status_code == 200
    slack = locked_client.post("/slack/events", content=b"{}")
    assert slack.status_code != 303


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
    assert locked_client.get("/docs").status_code == 200
    created = locked_client.post("/jobs", json={"repository": "acme/web", "request": "hi"})
    assert created.status_code == 200

    logout = locked_client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert locked_client.get("/", follow_redirects=False).status_code == 303
