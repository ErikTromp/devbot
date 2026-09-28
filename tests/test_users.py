from __future__ import annotations

from pathlib import Path

import pytest

from app.users import CredentialUserError, load_users, settings_for_job


def test_load_users_yaml(tmp_path: Path) -> None:
    path = tmp_path / "users.yaml"
    path.write_text(
        "users:\n  - name: Erik\n    github_token: ghp_erik\n    cursor_api_key: cursor_erik\n",
        encoding="utf-8",
    )
    catalog = load_users(path)
    assert catalog.names() == ["Erik"]
    assert catalog.resolve("erik").github_token == "ghp_erik"
    assert catalog.name_map() == {"erik": "Erik"}


def test_load_users_missing_file(tmp_path: Path) -> None:
    assert load_users(tmp_path / "missing.yaml").names() == []


def test_settings_for_job_overlays_tokens(settings, tmp_path: Path) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Erik\n    github_token: ghp_erik\n    cursor_api_key: cursor_erik\n",
        encoding="utf-8",
    )
    overlaid = settings_for_job(settings, "erik")
    assert overlaid.github_token == "ghp_erik"
    assert overlaid.cursor_api_key == "cursor_erik"
    assert overlaid.slack_bot_token == settings.slack_bot_token
    assert settings_for_job(settings, None) is settings


def test_settings_for_job_unknown_user(settings) -> None:
    assert not settings.users_file.is_file()
    assert settings.user_catalog().names() == []
    with pytest.raises(CredentialUserError, match="Unknown credential user"):
        settings_for_job(settings, "Erik")
