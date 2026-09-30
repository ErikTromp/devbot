from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.repos import RepoCatalog, parse_allowed_repos


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://devbot:devbot@localhost:5432/devbot",
        description="SQLAlchemy URL for PostgreSQL (source of truth).",
    )

    slack_bot_token: str = Field(default="", description="Slack bot OAuth token (xoxb-...).")
    slack_signing_secret: str = Field(default="", description="Slack request signing secret.")
    slack_app_token: str = Field(default="", description="Unused in Events API mode; reserved.")

    github_token: str = Field(default="", description="GitHub PAT or installation token.")
    github_webhook_secret: str = Field(default="", description="GitHub webhook HMAC secret.")
    default_github_org: str = Field(default="", description="Org used when Slack says a short repo name.")
    default_github_branch: str = Field(default="main", description="Base branch for worktrees/PRs.")
    github_api_url: str = Field(default="https://api.github.com", description="GitHub API base URL.")
    github_project_id: str = Field(
        default="",
        description="Optional GitHub Project V2 node id (PVT_...). Empty = find or create a user project named Devbot.",
    )

    cursor_api_key: str = Field(default="", description="Cursor API key for headless CLI.")
    cursor_cli_bin: str = Field(default="agent", description="Cursor CLI binary name or path.")
    cursor_model: str = Field(default="", description="Optional --model passed to Cursor CLI.")
    cursor_timeout_seconds: int = Field(default=900, description="Cursor subprocess timeout.")
    cursor_sandbox: str = Field(default="", description="Optional --sandbox enabled|disabled.")

    copilot_github_token: str = Field(
        default="",
        description="Fine-grained PAT or Copilot OAuth token for headless Copilot CLI.",
    )
    copilot_cli_bin: str = Field(default="copilot", description="Copilot CLI binary name or path.")
    copilot_model: str = Field(default="", description="Optional --model passed to Copilot CLI.")
    copilot_timeout_seconds: int = Field(default=900, description="Copilot subprocess timeout.")
    coding_agent: str = Field(
        default="",
        description="cursor or copilot. Empty = infer from which coding key is set.",
    )

    agent_workspace: Path = Field(
        default=Path("./agent-workspace"),
        description="Root for repo caches and job worktrees.",
    )
    allowed_repos: str = Field(
        default="",
        description="Comma-separated allowlist: owner/repo;alias or owner;alias.",
    )
    users_file: Path = Field(default=Path("./users.yaml"), description="YAML file of named GitHub and coding credentials.")

    github_assignee: str = Field(default="", description="Optional GitHub username assigned on phase start.")

    max_agent_attempts: int = Field(default=3, description="Max coding/review loops before FAILED.")
    worker_id: str = Field(default="", description="Override worker identity; default host:pid.")
    worker_poll_seconds: float = Field(default=2.0, description="Idle poll interval.")
    job_lease_seconds: int = Field(default=300, description="Claim lease; expired jobs are reclaimable.")
    test_timeout_seconds: int = Field(default=300, description="Detected test-runner timeout.")
    slack_max_age_seconds: int = Field(default=300, description="Reject older Slack signatures.")

    log_level: str = Field(default="INFO", description="Root log level.")
    prompts_dir: Path = Field(default=Path("prompts"), description="Version-controlled prompts.")

    dashboard_username: str = Field(description="Dashboard login username. Required.")
    dashboard_password: str = Field(description="Dashboard login password. Required.")
    base_url: str = Field(
        default="",
        description="Public dashboard origin, no trailing slash. Slack links to {BASE_URL}/tickets/DEV-N.",
    )

    @field_validator("dashboard_username", "dashboard_password")
    @classmethod
    def _require_dashboard_credential(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("DASHBOARD_USERNAME and DASHBOARD_PASSWORD are required")
        return cleaned

    def session_secret(self) -> str:
        return self.dashboard_password

    def session_https_only(self) -> bool:
        """Secure session cookies whenever the public host is not this machine."""
        host = (urlparse(self.base_url.strip()).hostname or "").lower().rstrip(".")
        return host not in {"", "localhost", "127.0.0.1", "::1"}

    def ticket_url(self, display_id: str) -> str:
        base = self.base_url.strip().rstrip("/")
        return f"{base}/tickets/{display_id}" if base else ""

    def repo_catalog(self) -> RepoCatalog:
        return parse_allowed_repos(self.allowed_repos, self.default_github_org)

    def allowed_repo_list(self) -> list[str]:
        return [entry.repository for entry in self.repo_catalog().entries]

    def allowed_repo_set(self) -> set[str]:
        names: set[str] = set()
        for entry in self.repo_catalog().entries:
            names.add(entry.repository.lower())
            names.add(entry.alias.lower())
            names.add(entry.repository.split("/")[-1].lower())
        return names

    def resolved_coding_agent(self) -> str:
        from app.users import resolve_coding_agent

        return resolve_coding_agent(self.cursor_api_key, self.copilot_github_token, self.coding_agent)

    def coding_agent_label(self) -> str:
        return "Copilot" if self.resolved_coding_agent() == "copilot" else "Cursor"

    def resolved_users_file(self) -> Path:
        configured = Path(self.users_file)
        if configured.is_file():
            return configured
        if configured.is_absolute() or len(configured.parts) > 1:
            return configured
        sibling = Path(__file__).resolve().parents[1] / configured.name
        return sibling if sibling.is_file() else configured

    def user_catalog(self):
        from app.users import load_users

        return load_users(self.resolved_users_file())


_override: Settings | None = None


@lru_cache
def _cached_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    return _override or _cached_settings()


def override_settings(settings: Settings | None) -> None:
    global _override
    _override = settings
    _cached_settings.cache_clear()
