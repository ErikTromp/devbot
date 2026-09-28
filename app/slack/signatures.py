from __future__ import annotations

import time

from app.security.secrets import compare_signatures, hmac_sha256_hex


class SlackSignatureError(ValueError):
    pass


def slack_signature(secret: str, timestamp: str, body: bytes) -> str:
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    return "v0=" + hmac_sha256_hex(secret, basestring)


def verify_slack_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    provided: str,
    max_age_seconds: int = 300,
    now: float | None = None,
) -> None:
    if not secret:
        raise SlackSignatureError("Slack signing secret is not configured")
    if not timestamp.isdigit():
        raise SlackSignatureError("Invalid Slack timestamp")
    age = abs((now if now is not None else time.time()) - int(timestamp))
    if age > max_age_seconds:
        raise SlackSignatureError("Slack timestamp is too old")
    expected = slack_signature(secret, timestamp, body)
    if not provided or not compare_signatures(expected, provided):
        raise SlackSignatureError("Invalid Slack signature")
