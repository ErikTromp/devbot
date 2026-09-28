from __future__ import annotations

import logging
from typing import Any

from app.db.models import Job

logger = logging.getLogger(__name__)

_MAX_CHARS = 24000


def gather_ticket_context(job: Job, *, slack: Any, github: Any) -> str:
    """Live Slack thread + GitHub issue/PR comments. Not stored in Postgres."""
    blocks = [_slack_block(job, slack), _github_block(job, github)]
    text = "\n\n".join(block for block in blocks if block)
    return _trim(text) or "(no Slack thread or GitHub comments available)"


def format_slack_messages(messages: list[dict[str, Any]]) -> str:
    lines = []
    for msg in messages:
        user = msg.get("user") or msg.get("username") or ("devbot" if msg.get("bot_id") else "unknown")
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"- {user}: {text}")
    return "\n".join(lines)


def format_github_discussion(issue: dict[str, Any], comments: list[dict[str, Any]], *, heading: str) -> str:
    title = issue.get("title") or ""
    body = (issue.get("body") or "").strip()
    author = _login(issue.get("user"))
    parts = [f"## {heading}: {title}", f"Author: {author}", body or "(empty body)"]
    if comments:
        parts.append("### Comments")
        parts.extend(
            f"- {_login(item.get('user'))}: {(item.get('body') or '').strip()}"
            for item in comments
            if (item.get("body") or "").strip()
        )
    return "\n".join(parts)


def _slack_block(job: Job, slack: Any) -> str:
    if not job.slack_channel or not job.slack_thread_ts or slack is None:
        return ""
    try:
        messages = slack.list_thread_replies(job.slack_channel, job.slack_thread_ts)
    except Exception:
        logger.warning("slack_thread_fetch_failed", extra={"slack_channel": job.slack_channel, "slack_thread": job.slack_thread_ts})
        return ""
    formatted = format_slack_messages(messages or [])
    return f"## Slack thread\n{formatted}" if formatted else ""


def _github_block(job: Job, github: Any) -> str:
    if github is None:
        return ""
    parts = []
    if job.github_issue_number:
        parts.append(_fetch_discussion(github, job.repository, job.github_issue_number, f"GitHub issue #{job.github_issue_number}"))
    if job.pull_request_number and job.pull_request_number != job.github_issue_number:
        parts.append(_fetch_discussion(github, job.repository, job.pull_request_number, f"GitHub PR #{job.pull_request_number}"))
    return "\n\n".join(part for part in parts if part)


def _fetch_discussion(github: Any, repository: str, number: int, heading: str) -> str:
    try:
        issue = github.get_issue(repository, number)
        comments = github.list_issue_comments(repository, number)
    except Exception:
        logger.warning("github_discussion_fetch_failed", extra={"repository": repository, "number": number})
        return ""
    return format_github_discussion(issue or {}, comments or [], heading=heading)


def _login(user: Any) -> str:
    if isinstance(user, dict):
        return str(user.get("login") or user.get("name") or "unknown")
    return str(user or "unknown")


def _trim(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_CHARS:
        return text
    return "… (truncated, keeping the latest Slack/GitHub text)\n\n" + text[-(_MAX_CHARS - 70) :].lstrip()
