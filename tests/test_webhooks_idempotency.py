from __future__ import annotations

import json
import time

from app.github.signatures import github_signature
from app.jobs.service import create_job
from app.slack.client import SlackClient
from app.slack.events import handle_slack_event
from app.slack.signatures import slack_signature


class NullSlack(SlackClient):
    def __init__(self, settings) -> None:
        self._token = ""
        self.messages: list[str] = []
        self.threads: list[str | None] = []

    def post_message(self, channel: str, text: str, *, thread_ts: str | None = None) -> dict:
        self.messages.append(text)
        self.threads.append(thread_ts)
        return {"ok": True}


def test_duplicate_slack_mention_reuses_job(session, settings) -> None:
    slack = NullSlack(settings)
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "user": "U1",
            "text": "<@Ubot> repo=myapp create CSV export",
            "channel": "C1",
            "ts": "111.222",
        },
    }
    first = handle_slack_event(session, payload, settings, slack)
    second = handle_slack_event(session, payload, settings, slack)
    assert first["created"] is True
    assert second["created"] is False
    assert first["job_id"] == second["job_id"]


def test_status_replies_to_the_new_mention(session, settings) -> None:
    job, _ = create_job(
        session,
        external_key="slack:old",
        repository="acme/myapp",
        request="old work",
        slack_channel="C1",
        slack_thread_ts="111.222",
        github_issue_number=9,
        github_issue_url="https://github.com/acme/myapp/issues/9",
    )
    session.commit()
    slack = NullSlack(settings)
    handle_slack_event(
        session,
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@Ubot> status 9",
                "channel": "C1",
                "ts": "999.001",
            },
        },
        settings,
        slack,
    )
    assert slack.threads[-1] == "999.001"


def test_slack_http_challenge_and_signature(client, settings) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    ts = str(int(time.time()))
    headers = {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": slack_signature(settings.slack_signing_secret, ts, body),
        "Content-Type": "application/json",
    }
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["challenge"] == "abc123"


def test_slack_http_rejects_bad_signature(client) -> None:
    response = client.post(
        "/slack/events",
        content=b"{}",
        headers={"X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=nope"},
    )
    assert response.status_code == 401


def test_github_http_duplicate_delivery(client, settings) -> None:
    body = b'{"action":"opened","repository":{"full_name":"acme/myapp"},"pull_request":{"number":1}}'
    headers = {
        "X-Hub-Signature-256": github_signature(settings.github_webhook_secret, body),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "dup-1",
    }
    first = client.post("/github/events", content=body, headers=headers)
    second = client.post("/github/events", content=body, headers=headers)
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True


def test_jobs_api(client, session) -> None:
    job, _ = create_job(session, external_key="api-1", repository="acme/myapp", request="do it")
    session.commit()
    listing = client.get(f"/jobs/{job.display_id}")
    assert listing.status_code == 200
    assert listing.json()["repository"] == "acme/myapp"
    events = client.get(f"/jobs/{job.id}/events")
    assert events.status_code == 200
    assert events.json()["events"]
