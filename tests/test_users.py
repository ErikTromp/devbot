from __future__ import annotations

from pathlib import Path

import pytest

from app.users import CredentialUserError, load_users, resolve_coding_agent, settings_for_job


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
    assert overlaid.coding_agent == "cursor"
    assert overlaid.slack_bot_token == settings.slack_bot_token
    assert settings_for_job(settings, None) is settings


def test_settings_for_job_overlays_copilot(settings) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Ada\n    github_token: ghp_ada\n    copilot_github_token: github_pat_ada\n",
        encoding="utf-8",
    )
    overlaid = settings_for_job(settings, "ada")
    assert overlaid.github_token == "ghp_ada"
    assert overlaid.copilot_github_token == "github_pat_ada"
    assert overlaid.cursor_api_key == ""
    assert overlaid.coding_agent == "copilot"
    assert overlaid.resolved_coding_agent() == "copilot"


def test_settings_for_job_both_keys_require_agent(settings) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Dual\n    github_token: ghp_dual\n    cursor_api_key: cursor_dual\n"
        "    copilot_github_token: github_pat_dual\n",
        encoding="utf-8",
    )
    with pytest.raises(CredentialUserError, match="both cursor_api_key and copilot_github_token"):
        settings_for_job(settings, "dual")


def test_settings_for_job_both_keys_with_agent(settings) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Dual\n    github_token: ghp_dual\n    cursor_api_key: cursor_dual\n"
        "    copilot_github_token: github_pat_dual\n    agent: copilot\n",
        encoding="utf-8",
    )
    overlaid = settings_for_job(settings, "dual")
    assert overlaid.coding_agent == "copilot"
    assert overlaid.cursor_api_key == "cursor_dual"
    assert overlaid.copilot_github_token == "github_pat_dual"


def test_settings_for_job_missing_coding_key(settings) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Empty\n    github_token: ghp_empty\n",
        encoding="utf-8",
    )
    with pytest.raises(CredentialUserError, match="missing cursor_api_key or copilot_github_token"):
        settings_for_job(settings, "empty")


def test_settings_for_job_unknown_user(settings) -> None:
    assert not settings.users_file.is_file()
    assert settings.user_catalog().names() == []
    with pytest.raises(CredentialUserError, match="Unknown credential user"):
        settings_for_job(settings, "Erik")


def test_resolve_coding_agent_infers_and_requires_explicit_when_both() -> None:
    assert resolve_coding_agent("cursor_key", "", "") == "cursor"
    assert resolve_coding_agent("", "github_pat_x", "") == "copilot"
    assert resolve_coding_agent("", "", "") == "cursor"
    with pytest.raises(CredentialUserError, match="both cursor_api_key and copilot_github_token"):
        resolve_coding_agent("cursor_key", "github_pat_x", "")
    assert resolve_coding_agent("cursor_key", "github_pat_x", "copilot") == "copilot"
    with pytest.raises(CredentialUserError, match="unknown agent"):
        resolve_coding_agent("cursor_key", "", "claude")
