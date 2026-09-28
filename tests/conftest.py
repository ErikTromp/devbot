from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings, override_settings
from app.db.models import Base
from app.db.session import create_db_engine, get_db, reset_engine
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    reset_engine()
    configured = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        slack_bot_token="",
        slack_signing_secret="slack-secret",
        github_token="",
        github_webhook_secret="gh-secret",
        default_github_org="acme",
        default_github_branch="main",
        allowed_repos="",
        cursor_api_key="",
        cursor_cli_bin="agent",
        agent_workspace=tmp_path / "workspace",
        prompts_dir=Path(__file__).resolve().parents[1] / "prompts",
        max_agent_attempts=2,
        job_lease_seconds=60,
        slack_max_age_seconds=300,
        dashboard_username="ops",
        dashboard_password="s3cret",
        users_file=tmp_path / "users.yaml",
        github_project_id="",
    )
    override_settings(configured)
    return configured


@pytest.fixture
def session(settings: Settings) -> Generator[Session, None, None]:
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        reset_engine()
        override_settings(None)


@pytest.fixture
def client(settings: Settings, session: Session) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_settings() -> Settings:
        return settings

    def override_db() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        login = test_client.post(
            "/login",
            data={"username": "ops", "password": "s3cret", "next": "/"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        yield test_client
