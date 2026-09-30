from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

_SECRET_RE = re.compile(
    r"(?i)(xox[baprs]-[A-Za-z0-9-]+|ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|"
    r"cursor_[A-Za-z0-9]+|sk-[A-Za-z0-9]+|Bearer\s+[A-Za-z0-9._\-]+)"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"
)

SECRET_ENV_KEYS = {
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_APP_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "CURSOR_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "DATABASE_URL",
    "DASHBOARD_PASSWORD",
}

# A subprocess may receive only the coding key it was started with.
SUBPROCESS_SECRET_ALLOW = {"CURSOR_API_KEY", "COPILOT_GITHUB_TOKEN"}

_SENSITIVE_ENV_RE = re.compile(
    r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|ACCESS_KEY|CREDENTIAL|WEBHOOK|PRIVATE_KEY|DATABASE_URL",
    re.IGNORECASE,
)


def redact_secrets(text: str | None) -> str:
    if not text:
        return ""
    redacted = _SECRET_RE.sub("[REDACTED]", text)
    return _ASSIGNMENT_RE.sub(lambda m: m.group(1) + "=[REDACTED]", redacted)


def compare_signatures(expected: str, provided: str) -> bool:
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def hmac_sha256_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


def is_secret_env_name(name: str) -> bool:
    return name.upper() in SECRET_ENV_KEYS or _SENSITIVE_ENV_RE.search(name) is not None


def sanitized_env(base: Mapping[str, str], extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for a child process with worker secrets removed.

    Inherited variables whose names look like tokens, passwords, or connection
    strings are dropped. ``extra`` may put back only ``CURSOR_API_KEY`` or
    ``COPILOT_GITHUB_TOKEN``. Do not log the result.
    """
    env = {key: value for key, value in base.items() if not is_secret_env_name(key)}
    allowed = {
        key: value
        for key, value in (extra or {}).items()
        if not is_secret_env_name(key) or key.upper() in SUBPROCESS_SECRET_ALLOW
    }
    env.update(allowed)
    return env


def safe_error(exc: BaseException) -> str:
    return redact_secrets(str(exc))[:2000]


def drop_secret_fields(payload: dict[str, Any]) -> dict[str, Any]:
    blocked = {key.lower() for key in SECRET_ENV_KEYS} | {"authorization", "cookie"}
    return {key: ("[REDACTED]" if key.lower() in blocked else value) for key, value in payload.items()}
