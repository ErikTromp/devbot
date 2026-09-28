from __future__ import annotations

from app.db.models import Job
from app.jobs.ticket_context import format_github_discussion, format_slack_messages, gather_ticket_context


def test_format_slack_and_github() -> None:
    slack = format_slack_messages(
        [
            {"user": "U1", "text": "create the X timeout ticket"},
            {"bot_id": "B1", "text": "I need more information"},
            {"user": "U1", "text": "just create it"},
        ]
    )
    assert "U1: create the X timeout ticket" in slack
    assert "devbot: I need more information" in slack
    gh = format_github_discussion(
        {"title": "X timeout", "body": "Stats die", "user": {"login": "erik"}},
        [{"user": {"login": "erik"}, "body": "Also check Calendar"}],
        heading="GitHub issue #9",
    )
    assert "GitHub issue #9: X timeout" in gh
    assert "Also check Calendar" in gh


def test_gather_reads_slack_and_issue_comments() -> None:
    job = Job(
        id=1,
        external_key="k",
        repository="acme/myapp",
        request="x",
        slack_channel="C1",
        slack_thread_ts="9.1",
        github_issue_number=9,
        pull_request_number=18,
    )

    class Slack:
        def list_thread_replies(self, channel, thread_ts):
            assert channel == "C1" and thread_ts == "9.1"
            return [{"user": "U1", "text": "thread note"}]

    class GitHub:
        def get_issue(self, repository, number):
            return {"title": f"item {number}", "body": f"body {number}", "user": {"login": "erik"}}

        def list_issue_comments(self, repository, number):
            return [{"user": {"login": "erik"}, "body": f"comment on {number}"}]

    text = gather_ticket_context(job, slack=Slack(), github=GitHub())
    assert "thread note" in text
    assert "comment on 9" in text
    assert "comment on 18" in text
