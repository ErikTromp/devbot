from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings

CODING_AGENTS = frozenset({"cursor", "copilot"})


class CredentialUserError(Exception):
    pass


@dataclass(frozen=True)
class CredentialUser:
    name: str
    github_token: str
    cursor_api_key: str = ""
    copilot_github_token: str = ""
    agent: str = ""


class UserCatalog:
    def __init__(self, users: list[CredentialUser] | None = None) -> None:
        self._users = list(users or [])
        self._by_lower = {user.name.lower(): user for user in self._users}

    def resolve(self, name: str | None) -> CredentialUser | None:
        if not name:
            return None
        return self._by_lower.get(name.strip().lower())

    def names(self) -> list[str]:
        return [user.name for user in self._users]

    def name_map(self) -> dict[str, str]:
        return {user.name.lower(): user.name for user in self._users}


def load_users(path: Path | str | None) -> UserCatalog:
    if path is None:
        return UserCatalog()
    file = Path(path)
    if not file.is_file():
        return UserCatalog()
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CredentialUserError(f"Could not read {file}: {exc}") from exc
    rows: list[Any] = []
    if isinstance(raw, dict):
        rows = list(raw.get("users") or [])
    elif isinstance(raw, list):
        rows = raw
    users: list[CredentialUser] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        users.append(
            CredentialUser(
                name=name,
                github_token=str(row.get("github_token") or "").strip(),
                cursor_api_key=str(row.get("cursor_api_key") or "").strip(),
                copilot_github_token=str(row.get("copilot_github_token") or "").strip(),
                agent=str(row.get("agent") or "").strip(),
            )
        )
    return UserCatalog(users)


def resolve_coding_agent(
    cursor_api_key: str,
    copilot_github_token: str,
    coding_agent: str = "",
    *,
    required: bool = False,
    user_name: str | None = None,
) -> str:
    who = f"Credential user {user_name!r}" if user_name else "Process credentials"
    explicit = (coding_agent or "").strip().lower()
    has_cursor = bool((cursor_api_key or "").strip())
    has_copilot = bool((copilot_github_token or "").strip())
    if explicit:
        if explicit not in CODING_AGENTS:
            raise CredentialUserError(f"{who} has unknown agent {coding_agent!r}. Use cursor or copilot.")
        if explicit == "cursor" and not has_cursor:
            raise CredentialUserError(f"{who} selected cursor but is missing cursor_api_key.")
        if explicit == "copilot" and not has_copilot:
            raise CredentialUserError(f"{who} selected copilot but is missing copilot_github_token.")
        return explicit
    if has_cursor and has_copilot:
        raise CredentialUserError(
            f"{who} has both cursor_api_key and copilot_github_token. Set agent: cursor or agent: copilot."
        )
    if has_cursor:
        return "cursor"
    if has_copilot:
        return "copilot"
    if required:
        raise CredentialUserError(f"{who} is missing cursor_api_key or copilot_github_token.")
    return "cursor"


def settings_for_job(settings: Settings, credential_user: str | None) -> Settings:
    if not credential_user:
        return settings
    user = load_users(settings.resolved_users_file()).resolve(credential_user)
    if user is None:
        raise CredentialUserError(f"Unknown credential user {credential_user!r}. Add them to users.yaml.")
    if not user.github_token:
        raise CredentialUserError(f"Credential user {user.name!r} is missing github_token.")
    coding_agent = resolve_coding_agent(
        user.cursor_api_key,
        user.copilot_github_token,
        user.agent,
        required=True,
        user_name=user.name,
    )
    return settings.model_copy(
        update={
            "github_token": user.github_token,
            "cursor_api_key": user.cursor_api_key,
            "copilot_github_token": user.copilot_github_token,
            "coding_agent": coding_agent,
        }
    )
