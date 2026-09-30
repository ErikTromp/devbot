from __future__ import annotations

import pytest

from app.jobs.gates import empty_phase_results, mark_phase
from app.jobs.models import JobStatus, PhaseStatus, PipelinePhase
from app.db.models import Job
from app.jobs.service import create_job
from app.slack.client import SlackClient
from app.slack.events import SlackCommandError, format_status_board, handle_slack_event
from app.slack.help import format_help


class NullSlack(SlackClient):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def post_message(self, channel: str, text: str, *, thread_ts: str | None = None) -> dict:
        self.messages.append(text)
        return {"ok": True}


def _mention(text: str) -> dict:
    return {"type": "event_callback", "event": {"type": "app_mention", "user": "U1", "text": text, "channel": "C1", "ts": "9.1"}}


def test_help_and_status_board(session, settings) -> None:
    slack = NullSlack()
    handle_slack_event(session, _mention("<@Ubot> help"), settings, slack)
    help_text = slack.messages[-1]
    assert "create" in help_text
    assert "implement 9" in help_text
    assert "I need a repository" not in help_text
    handle_slack_event(session, _mention("<@Ubot|devbot> help"), settings, slack)
    assert "GitHub-issue pipeline" in slack.messages[-1]
    assert "Supported repos" in slack.messages[-1]
    assert "acme" in slack.messages[-1]
    result = handle_slack_event(session, _mention("<@Ubot> status"), settings, slack)
    assert result["command"] == "status"
    assert "pipeline" in slack.messages[-1].lower() or "no pipeline" in slack.messages[-1].lower()
    listed = format_help(settings.model_copy(update={"allowed_repos": "myapp,acme/other"}))
    assert "`acme/myapp`" in listed
    assert "`acme/other`" in listed


def test_autopilot_on_existing_ticket_queues_next_step(session, settings) -> None:
    results = mark_phase(empty_phase_results(), PipelinePhase.CREATE, PhaseStatus.PASSED)
    job, _ = create_job(
        session,
        external_key="auto",
        repository="acme/myapp",
        request="work",
        slack_channel="C1",
        slack_thread_ts="1.1",
        github_issue_number=9,
        github_issue_url="https://github.com/acme/myapp/issues/9",
        phase_results=results,
    )
    job.status = JobStatus.IDLE.value
    session.commit()
    slack = NullSlack()
    result = handle_slack_event(session, _mention("<@Ubot> autopilot 9"), settings, slack)
    session.refresh(job)
    assert result["phase"] == "implement"
    assert job.status == JobStatus.QUEUED.value
    assert (job.specification or {}).get("autopilot") is True
    assert "implement" in slack.messages[-1]


def test_dev_id_rejected(session, settings) -> None:
    slack = NullSlack()
    with pytest.raises(SlackCommandError):
        handle_slack_event(session, _mention("<@Ubot> test DEV-9"), settings, slack)
    assert slack.messages and ("DEV-9" in slack.messages[-1] or "GitHub issue" in slack.messages[-1])


def test_skip_requires_prior_phase(session, settings) -> None:
    results = mark_phase(empty_phase_results(), PipelinePhase.CREATE, PhaseStatus.PASSED)
    job, _ = create_job(
        session,
        external_key="s1",
        repository="acme/myapp",
        request="work",
        slack_channel="C1",
        slack_thread_ts="1.1",
        github_issue_number=9,
        github_issue_url="https://github.com/acme/myapp/issues/9",
        phase_results=results,
    )
    job.status = JobStatus.IDLE.value
    session.commit()
    slack = NullSlack()
    with pytest.raises(SlackCommandError):
        handle_slack_event(session, _mention("<@Ubot> test skip 9"), settings, slack)
    session.refresh(job)
    assert job.phase_results["test"]["status"] != "skipped"
    handle_slack_event(session, _mention("<@Ubot> implement skip 9"), settings, slack)
    session.refresh(job)
    assert job.phase_results["implement"]["status"] == "skipped"
    assert "security skip 9" in slack.messages[-1]
    with pytest.raises(SlackCommandError):
        handle_slack_event(session, _mention("<@Ubot> test skip 9"), settings, slack)
    handle_slack_event(session, _mention("<@Ubot> security skip 9"), settings, slack)
    handle_slack_event(session, _mention("<@Ubot> architect skip 9"), settings, slack)
    handle_slack_event(session, _mention("<@Ubot> test skip 9"), settings, slack)
    session.refresh(job)
    assert job.phase_results["test"]["status"] == "skipped"
    assert "document skip 9" in slack.messages[-1]


def test_status_board_includes_issue_title(session, settings) -> None:
    results = mark_phase(empty_phase_results(), PipelinePhase.CREATE, PhaseStatus.PASSED)
    job, _ = create_job(
        session,
        external_key="board-1",
        repository="acme/myapp",
        request="redesign landing",
        github_issue_number=9,
        github_issue_url="https://github.com/acme/myapp/issues/9",
        phase_results=results,
        specification={"title": "Redesign landing"},
    )
    job.status = JobStatus.IDLE.value
    session.commit()
    slack = NullSlack()
    handle_slack_event(session, _mention("<@Ubot> status"), settings, slack)
    board = slack.messages[-1]
    assert "#9" in board
    assert "Redesign landing" in board
    assert "implement skip 9" in board
    assert format_status_board([job])


def test_remove_closes_github_and_deletes_job(session, settings, monkeypatch) -> None:
    results = mark_phase(empty_phase_results(), PipelinePhase.CREATE, PhaseStatus.PASSED)
    job, _ = create_job(
        session,
        external_key="rm-1",
        repository="acme/myapp",
        request="old work",
        slack_channel="C1",
        slack_thread_ts="1.1",
        github_issue_number=9,
        github_issue_url="https://github.com/acme/myapp/issues/9",
        phase_results=results,
        specification={"title": "Old work"},
    )
    job.branch_name = "agent/issue-9"
    job.pull_request_number = 18
    job.pull_request_url = "https://github.com/acme/myapp/pull/18"
    session.commit()
    job_id = job.id
    calls: list[tuple] = []

    class FakeGH:
        def __init__(self, _settings) -> None:
            pass

        def close_issue(self, repository, number):
            calls.append(("issue", repository, number))
            return {}

        def close_pull_request(self, repository, number):
            calls.append(("pr", repository, number))
            return {}

        def delete_branch(self, repository, branch):
            calls.append(("branch", repository, branch))

    monkeypatch.setattr("app.slack.events.GitHubClient", FakeGH)
    monkeypatch.setattr("app.jobs.cleanup.cleanup_local", lambda *_a, **_k: ["worktree"])
    slack = NullSlack()
    result = handle_slack_event(session, _mention("<@Ubot> remove 9"), settings, slack)
    assert result["removed"] is True
    assert session.get(Job, job_id) is None
    assert ("issue", "acme/myapp", 9) in calls
    assert ("pr", "acme/myapp", 18) in calls
    assert ("branch", "acme/myapp", "agent/issue-9") in calls
    assert "Removed #9" in slack.messages[-1]
    assert "Old work" in slack.messages[-1]


def _thread_mention(text: str, *, ts: str = "9.2", thread_ts: str = "9.1") -> dict:
    return {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "user": "U1",
            "text": text,
            "channel": "C1",
            "ts": ts,
            "thread_ts": thread_ts,
        },
    }


def test_remove_dev_id_without_github_issue(session, settings, monkeypatch) -> None:
    job, _ = create_job(
        session,
        external_key="rm-dev",
        repository="acme/myapp",
        request="still refining",
        slack_channel="C1",
        slack_thread_ts="1.1",
        pipeline_phase=PipelinePhase.CREATE,
        specification={"title": "Still refining"},
    )
    session.commit()
    job_id = job.id
    monkeypatch.setattr("app.jobs.cleanup.cleanup_github", lambda *_a, **_k: [])
    monkeypatch.setattr("app.jobs.cleanup.cleanup_local", lambda *_a, **_k: [])

    class FakeGH:
        def __init__(self, _settings) -> None:
            pass

    monkeypatch.setattr("app.slack.events.GitHubClient", FakeGH)
    slack = NullSlack()
    result = handle_slack_event(session, _mention(f"<@Ubot> remove DEV-{job_id}"), settings, slack)
    assert result["removed"] is True
    assert session.get(Job, job_id) is None
    assert f"Removed DEV-{job_id}" in slack.messages[-1]


def test_awaiting_thread_just_create_resumes(session, settings) -> None:
    job, _ = create_job(
        session,
        external_key="await-1",
        repository="acme/myapp",
        request="X stats timeout",
        slack_channel="C1",
        slack_thread_ts="9.1",
        pipeline_phase=PipelinePhase.CREATE,
        specification={"title": "X stats timeout"},
    )
    job.status = JobStatus.AWAITING_INPUT.value
    session.commit()
    slack = NullSlack()
    result = handle_slack_event(
        session,
        _thread_mention(
            "<@Ubot> The connection just dies. Just create the ticket, no more questions"
        ),
        settings,
        slack,
    )
    session.refresh(job)
    assert result["command"] == "clarify"
    assert job.status == JobStatus.QUEUED.value
    assert job.specification["force_continue"] is True
    assert any("Just create the ticket" in item for item in job.specification["answers"])
    assert "I need a repository" not in "\n".join(slack.messages)


def test_commit_command_queues_force_commit(session, settings) -> None:
    results = mark_phase(empty_phase_results(), PipelinePhase.CREATE, PhaseStatus.PASSED)
    results = mark_phase(results, PipelinePhase.IMPLEMENT, PhaseStatus.PASSED)
    results = mark_phase(results, PipelinePhase.TEST, PhaseStatus.PASSED)
    results = mark_phase(results, PipelinePhase.SECURITY, PhaseStatus.PASSED)
    job, _ = create_job(
        session,
        external_key="commit-cmd",
        repository="acme/myapp",
        request="Landscape",
        slack_channel="C1",
        slack_thread_ts="1.1",
        github_issue_number=49,
        github_issue_url="https://github.com/acme/myapp/issues/49",
        phase_results=results,
        pipeline_phase=PipelinePhase.SECURITY,
    )
    job.status = JobStatus.IDLE.value
    job.worktree_path = "/workspace/jobs/DEV-1/worktree"
    job.branch_name = "agent/issue-49"
    session.commit()
    slack = NullSlack()
    result = handle_slack_event(session, _mention("<@Ubot> commit 49"), settings, slack)
    session.refresh(job)
    assert result["command"] == "commit"
    assert job.status == JobStatus.QUEUED.value
    assert job.specification["pending_action"] == "commit"
    assert job.pipeline_phase == "security"
    assert any("Force-commit queued" in msg for msg in slack.messages)


def test_create_stores_named_credentials(session, settings) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Erik\n    github_token: ghp_erik\n    cursor_api_key: cursor_erik\n",
        encoding="utf-8",
    )
    slack = NullSlack()
    result = handle_slack_event(
        session,
        _mention("<@Ubot> create Erik acme/myapp redesign the landing page"),
        settings,
        slack,
    )
    job = session.get(Job, result["job_id"])
    assert job is not None
    assert job.credential_user == "Erik"
    assert job.repository == "acme/myapp"
    assert job.request == "redesign the landing page"
