from __future__ import annotations

import pytest

from app.slack.signatures import SlackSignatureError, slack_signature, verify_slack_signature


def test_valid_slack_signature() -> None:
    body = b'{"type":"url_verification"}'
    ts = "1710000000"
    sig = slack_signature("slack-secret", ts, body)
    verify_slack_signature(secret="slack-secret", timestamp=ts, body=body, provided=sig, now=1710000001)


def test_rejects_bad_slack_signature() -> None:
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            secret="slack-secret",
            timestamp="1710000000",
            body=b"{}",
            provided="v0=deadbeef",
            now=1710000001,
        )


def test_rejects_old_slack_timestamp() -> None:
    body = b"{}"
    ts = "1000"
    sig = slack_signature("slack-secret", ts, body)
    with pytest.raises(SlackSignatureError, match="too old"):
        verify_slack_signature(secret="slack-secret", timestamp=ts, body=body, provided=sig, now=2000, max_age_seconds=60)
