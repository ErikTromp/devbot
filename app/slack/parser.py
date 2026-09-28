from __future__ import annotations

import re
from dataclasses import dataclass

from app.jobs.models import SlackCommand, parse_job_display_id
from app.repos import RepoCatalog, normalize_repo

_CREATE_USER_COMMANDS = {SlackCommand.CREATE, SlackCommand.AUTOPILOT}

_MENTION_RE = re.compile(r"<@!?[A-Z0-9]+(?:\|[^>]+)?>", re.IGNORECASE)
_AT_NAME_RE = re.compile(r"^@\w+\s+", re.IGNORECASE)
_REPO_EQ_RE = re.compile(r"\brepo=([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)", re.IGNORECASE)
_SKIP_RE = re.compile(r"\bskip\b", re.IGNORECASE)
_DEV_RE = re.compile(r"\bDEV-\d+\b", re.IGNORECASE)
_ISSUE_RE = re.compile(r"(?<![A-Za-z-])#(\d+)\b|(?<![A-Za-z-#])(\d+)\b")
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

_COMMAND_ALIASES: dict[str, SlackCommand] = {
    "create": SlackCommand.CREATE,
    "implement": SlackCommand.IMPLEMENT,
    "test": SlackCommand.TEST,
    "security": SlackCommand.SECURITY,
    "architect": SlackCommand.ARCHITECT,
    "document": SlackCommand.DOCUMENT,
    "commit": SlackCommand.COMMIT,
    "autopilot": SlackCommand.AUTOPILOT,
    "help": SlackCommand.HELP,
    "status": SlackCommand.STATUS,
    "cancel": SlackCommand.CANCEL,
    "remove": SlackCommand.REMOVE,
    "delete": SlackCommand.REMOVE,
    "retry": SlackCommand.RETRY,
    "ping": SlackCommand.PING,
    "hello": SlackCommand.PING,
    "health": SlackCommand.PING,
}

_RESERVED = set(_COMMAND_ALIASES) | {"skip"}

_ISSUE_COMMANDS = {
    SlackCommand.IMPLEMENT,
    SlackCommand.TEST,
    SlackCommand.SECURITY,
    SlackCommand.ARCHITECT,
    SlackCommand.DOCUMENT,
    SlackCommand.COMMIT,
    SlackCommand.AUTOPILOT,
    SlackCommand.CANCEL,
    SlackCommand.REMOVE,
    SlackCommand.RETRY,
    SlackCommand.STATUS,
}


@dataclass(frozen=True)
class ParsedSlackCommand:
    command: SlackCommand | None
    repository: str | None
    request: str
    issue_number: int | None
    skip: bool
    used_dev_id: bool
    job_id: int | None
    credential_user: str | None
    raw_text: str


def strip_mentions(text: str) -> str:
    cleaned = _MENTION_RE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _AT_NAME_RE.sub("", cleaned).strip()
    return cleaned


def _bare_token(token: str) -> str:
    text = token.strip()
    if text.lower().startswith("repo="):
        return text[5:]
    return text


def _is_repo_token(token: str, catalog: RepoCatalog, default_org: str) -> bool:
    bare = _bare_token(token)
    if not bare or bare.lower() in _RESERVED:
        return False
    if catalog.resolve(bare, default_org):
        if not catalog.empty:
            return True
        return "/" in bare or token.lower().startswith("repo=")
    return bool(token.lower().startswith("repo=") and normalize_repo(bare, default_org))


def _is_user_token(token: str, user_names: dict[str, str]) -> bool:
    return _bare_token(token).lower() in user_names


def _resolve_command(
    tokens: list[str],
    catalog: RepoCatalog,
    default_org: str,
    user_names: dict[str, str] | None = None,
) -> tuple[SlackCommand | None, str | None]:
    names = user_names or {}
    for token in tokens:
        bare = _bare_token(token).lower()
        if bare in _COMMAND_ALIASES:
            return _COMMAND_ALIASES[bare], token
        if _is_repo_token(token, catalog, default_org) or _is_user_token(token, names):
            continue
        return None, None
    return None, None


def _resolve_credential_user(
    tokens: list[str],
    command: SlackCommand | None,
    catalog: RepoCatalog,
    default_org: str,
    user_names: dict[str, str],
) -> tuple[str | None, str | None]:
    if command not in _CREATE_USER_COMMANDS or not user_names:
        return None, None
    for token in tokens:
        if _bare_token(token).lower() in _COMMAND_ALIASES:
            continue
        if _is_repo_token(token, catalog, default_org):
            continue
        canonical = user_names.get(_bare_token(token).lower())
        if canonical:
            return canonical, token
        return None, None
    return None, None


def _resolve_repository(tokens: list[str], catalog: RepoCatalog, default_org: str) -> tuple[str | None, str | None]:
    for token in tokens:
        if not _is_repo_token(token, catalog, default_org):
            continue
        repo = catalog.resolve(_bare_token(token), default_org) or normalize_repo(_bare_token(token), default_org)
        if repo:
            return repo, token
    return None, None


def parse_slack_text(
    text: str,
    default_org: str = "",
    catalog: RepoCatalog | None = None,
    user_names: dict[str, str] | None = None,
) -> ParsedSlackCommand:
    raw = text or ""
    cleaned = strip_mentions(raw)
    repos = catalog or RepoCatalog()
    names = {key.lower(): value for key, value in (user_names or {}).items()}
    tokens = cleaned.split()
    command, command_token = _resolve_command(tokens, repos, default_org, names)
    skip = bool(_SKIP_RE.search(cleaned))
    dev_match = _DEV_RE.search(cleaned)
    used_dev_id = bool(dev_match)
    job_id = parse_job_display_id(dev_match.group(0)) if dev_match else None

    issue_number = None
    if not used_dev_id:
        issue_match = _ISSUE_RE.search(cleaned)
        if issue_match:
            issue_number = int(issue_match.group(1) or issue_match.group(2))

    if command in _ISSUE_COMMANDS and used_dev_id:
        issue_number = None

    repository, repo_token = _resolve_repository(tokens, repos, default_org)
    if repository is None:
        eq = _REPO_EQ_RE.search(cleaned)
        if eq:
            repository = repos.resolve(eq.group(1), default_org) or normalize_repo(eq.group(1), default_org)
            repo_token = eq.group(0)

    credential_user, credential_token = _resolve_credential_user(tokens, command, repos, default_org, names)

    request = cleaned
    if repo_token:
        request = request.replace(repo_token, " ", 1)
    if command_token:
        request = request.replace(command_token, " ", 1)
    if credential_token:
        request = request.replace(credential_token, " ", 1)
    if skip:
        request = _SKIP_RE.sub(" ", request, count=1)
    if issue_number is not None:
        request = re.sub(rf"#?{issue_number}\b", " ", request, count=1)
    request = _DEV_RE.sub(" ", request)
    request = _REPO_EQ_RE.sub(" ", request)
    request = re.sub(r"\s+", " ", request).strip()
    return ParsedSlackCommand(
        command=command,
        repository=repository,
        request=request,
        issue_number=issue_number,
        skip=skip,
        used_dev_id=used_dev_id,
        job_id=job_id,
        credential_user=credential_user,
        raw_text=raw,
    )
