from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Job
from app.github.client import GitHubClient
from app.github.issues import specification_from_issue
from app.jobs.board import next_action_lines
from app.jobs.gates import can_run_phase, empty_phase_results, mark_phase, next_phase
from app.jobs.models import (
    PHASE_COMMANDS,
    JobEventType,
    JobStatus,
    PhaseStatus,
    PipelinePhase,
    SlackCommand,
    command_to_phase,
)
from app.jobs.cleanup import remove_job
from app.jobs.service import (
    add_event,
    attach_github_issue,
    create_job,
    find_job_by_thread,
    find_jobs_by_issue,
    get_job,
    list_pipeline_jobs,
    release_lease,
    transition_job,
)
from app.jobs.state_machine import InvalidTransition
from app.slack.client import SlackClient
from app.slack.help import format_help
from app.slack.parser import parse_slack_text, strip_mentions

logger = logging.getLogger(__name__)

_FORCE_CONTINUE_RE = re.compile(
    r"just create(?: the ticket)?|no more questions|stop asking|enough questions|"
    r"create the ticket|just open(?: the)?(?: ticket)?|don'?t ask|do not ask",
    re.IGNORECASE,
)

_THREAD_OVERRIDE_COMMANDS = {
    SlackCommand.HELP,
    SlackCommand.PING,
    SlackCommand.CANCEL,
    SlackCommand.REMOVE,
    SlackCommand.RETRY,
    SlackCommand.STATUS,
    SlackCommand.IMPLEMENT,
    SlackCommand.TEST,
    SlackCommand.SECURITY,
    SlackCommand.ARCHITECT,
    SlackCommand.DOCUMENT,
    SlackCommand.COMMIT,
    SlackCommand.AUTOPILOT,
}


class SlackCommandError(ValueError):
    pass


def slack_external_key(team_id: str | None, channel: str, ts: str) -> str:
    return f"slack:{team_id or 'unknown'}:{channel}:{ts}"


def handle_slack_event(session: Session, payload: dict[str, Any], settings: Settings, slack: SlackClient) -> dict[str, Any]:
    event_type = payload.get("type")
    if event_type == "url_verification":
        return {"challenge": payload.get("challenge")}
    if event_type != "event_callback":
        return {"ok": True, "ignored": True}

    event = payload.get("event") or {}
    if event.get("type") != "app_mention" and not _is_control_message(event):
        return {"ok": True, "ignored": True}
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return {"ok": True, "ignored": True}

    text = event.get("text") or ""
    channel = event.get("channel") or ""
    incoming_ts = event.get("ts") or ""
    job_thread_ts = event.get("thread_ts") or incoming_ts
    user = event.get("user")
    parsed = parse_slack_text(
        text,
        settings.default_github_org,
        catalog=settings.repo_catalog(),
        user_names=settings.user_catalog().name_map(),
    )

    logger.info(
        "slack_event",
        extra={"event": event.get("type"), "command": getattr(parsed.command, "value", None), "channel": channel},
    )
    thread_job = find_job_by_thread(session, channel, job_thread_ts)
    if (
        thread_job
        and thread_job.status == JobStatus.AWAITING_INPUT.value
        and parsed.command not in _THREAD_OVERRIDE_COMMANDS
    ):
        answers = strip_mentions(text)
        if answers:
            return _resume_with_answers(session, thread_job, answers, slack, channel, incoming_ts)
    if parsed.command == SlackCommand.PING:
        slack.post_message(
            channel,
            "pong — API reached this channel. Jobs and the coding agent run in the host worker, not this reply.",
            thread_ts=incoming_ts or None,
        )
        return {"ok": True, "command": "ping"}
    if parsed.command == SlackCommand.HELP or parsed.command is None:
        slack.post_message(channel, format_help(settings), thread_ts=incoming_ts or None)
        if parsed.command is None:
            raise SlackCommandError("unknown command")
        return {"ok": True, "command": "help"}
    if parsed.command == SlackCommand.STATUS and parsed.issue_number is None and parsed.job_id is None:
        slack.post_message(channel, format_status_board(list_pipeline_jobs(session)), thread_ts=incoming_ts or None)
        return {"ok": True, "command": "status"}
    if parsed.command == SlackCommand.CREATE:
        return _create_ticket_job(session, payload, event, parsed, settings, slack, channel, job_thread_ts, user)
    if parsed.command == SlackCommand.COMMIT:
        return _handle_commit_command(session, parsed, settings, slack, channel, incoming_ts, job_thread_ts, user)
    if parsed.command == SlackCommand.AUTOPILOT:
        return _handle_autopilot_command(session, payload, event, parsed, settings, slack, channel, incoming_ts, job_thread_ts, user)
    if parsed.command in PHASE_COMMANDS:
        return _handle_phase_command(session, parsed, settings, slack, channel, incoming_ts, job_thread_ts, user)
    return _handle_control_command(session, parsed, settings, slack, channel, incoming_ts)


def _is_control_message(event: dict[str, Any]) -> bool:
    if event.get("type") != "message":
        return False
    text = (event.get("text") or "").strip().lower()
    return text.startswith(("cancel ", "remove ", "delete ", "retry ", "status ", "help", "ping", "hello", "health"))


def _allowed_repo(settings: Settings, repository: str) -> bool:
    return settings.repo_catalog().allows(repository)


def _handle_autopilot_command(
    session: Session,
    payload: dict[str, Any],
    event: dict[str, Any],
    parsed,
    settings: Settings,
    slack: SlackClient,
    channel: str,
    incoming_ts: str,
    thread_ts: str,
    user: str | None,
) -> dict[str, Any]:
    if parsed.issue_number is None and parsed.job_id is None:
        result = _create_ticket_job(
            session, payload, event, parsed, settings, slack, channel, thread_ts, user, autopilot=True
        )
        return {**result, "command": "autopilot"}
    if parsed.job_id is not None and parsed.issue_number is None:
        job = get_job(session, parsed.job_id)
        if job is None:
            slack.post_message(channel, f"I could not find `autopilot DEV-{parsed.job_id}`.", thread_ts=incoming_ts or None)
            raise SlackCommandError("unknown job")
    else:
        job = _resolve_issue_job(session, parsed, settings, slack, channel, incoming_ts, thread_ts, user)
    return _queue_autopilot_job(session, job, parsed, slack, channel, incoming_ts, thread_ts)


def _queue_autopilot_job(session, job, parsed, slack: SlackClient, channel: str, incoming_ts: str, thread_ts: str) -> dict[str, Any]:
    if job.status == JobStatus.RUNNING.value:
        slack.post_message(channel, f"{job.issue_ref} is already running `{job.pipeline_phase}`.", thread_ts=incoming_ts or None)
        raise SlackCommandError("job running")
    if job.status == JobStatus.COMPLETED.value:
        slack.post_message(channel, f"{job.issue_ref} is already completed.", thread_ts=incoming_ts or None)
        raise SlackCommandError("job completed")
    if job.status == JobStatus.CANCELLED.value:
        slack.post_message(channel, f"{job.issue_ref} is cancelled. Reply `retry {job.issue_ref.lstrip('#')}` first.", thread_ts=incoming_ts or None)
        raise SlackCommandError("job cancelled")
    phase = next_phase(job.phase_results)
    if phase is None:
        slack.post_message(channel, f"{job.issue_ref} has no remaining steps.", thread_ts=incoming_ts or None)
        raise SlackCommandError("pipeline complete")
    allowed, reason = can_run_phase(job.phase_results, phase)
    if not allowed:
        slack.post_message(channel, f"{job.issue_ref}: {reason}", thread_ts=incoming_ts or None)
        raise SlackCommandError(reason or "phase gated")
    job.specification = {**(job.specification or {}), "autopilot": True}
    if job.slack_channel is None:
        job.slack_channel = channel
    if job.slack_thread_ts is None:
        job.slack_thread_ts = thread_ts
    if parsed.request:
        extra = parsed.request.strip()
        if extra:
            job.request = f"{job.request}\n\nFollow-up: {extra}"
    job.pipeline_phase = phase.value
    job.current_stage = phase.value
    job.attempt = 0
    job.last_error = None
    job.completed_at = None
    release_lease(session, job)
    if job.status != JobStatus.QUEUED.value:
        transition_job(session, job, JobStatus.QUEUED, event_type=JobEventType.PHASE_STARTED, stage=phase.value, payload={"phase": phase.value, "autopilot": True})
    else:
        add_event(session, job, JobEventType.PHASE_STARTED, payload={"phase": phase.value, "autopilot": True})
    session.commit()
    slack.post_message(
        channel,
        format_progress(
            job,
            [
                f"→ Autopilot queued `{phase.value}` for {job.issue_ref}",
                "Remaining steps run in order: create → implement → test → security → architect → document.",
                "Each step commits and pushes before the next one starts.",
            ],
        ),
        thread_ts=incoming_ts or job.slack_thread_ts,
    )
    return {"ok": True, "job_id": job.id, "command": "autopilot", "phase": phase.value}


def _create_ticket_job(
    session: Session,
    payload: dict[str, Any],
    event: dict[str, Any],
    parsed,
    settings: Settings,
    slack: SlackClient,
    channel: str,
    ts: str,
    user: str | None,
    autopilot: bool = False,
) -> dict[str, Any]:
    if parsed.skip:
        slack.post_message(channel, "The create step cannot be skipped. It is the only command that invents a GitHub issue.", thread_ts=ts or None)
        raise SlackCommandError("cannot skip create")
    if not parsed.repository:
        slack.post_message(
            channel,
            "I need a repository. Example: `@devbot "
            + ("autopilot" if autopilot else "create")
            + " web redesign the landing page`",
            thread_ts=ts or None,
        )
        raise SlackCommandError("repository required")
    if not _allowed_repo(settings, parsed.repository):
        slack.post_message(channel, f"Repository `{parsed.repository}` is not in ALLOWED_REPOS.", thread_ts=ts or None)
        raise SlackCommandError("repository not allowed")
    if not parsed.request:
        slack.post_message(channel, "Tell me what the ticket should cover after `create`.", thread_ts=ts or None)
        raise SlackCommandError("request required")

    team_id = payload.get("team_id")
    event_ts = event.get("ts") or ts
    external_key = slack_external_key(team_id, channel, event_ts)
    job, created = create_job(
        session,
        external_key=external_key,
        repository=parsed.repository,
        request=parsed.request,
        slack_channel=channel,
        slack_thread_ts=ts or event_ts,
        requested_by=user,
        credential_user=parsed.credential_user,
        pipeline_phase=PipelinePhase.CREATE,
        specification={"autopilot": True} if autopilot else None,
    )
    if created:
        add_event(session, job, JobEventType.SLACK_REQUESTED, payload={"user": user, "channel": channel, "ts": event_ts})
        slack.post_message(channel, format_job_created(job), thread_ts=job.slack_thread_ts)
    return {"ok": True, "job_id": job.id, "created": created}


def _handle_phase_command(
    session: Session,
    parsed,
    settings: Settings,
    slack: SlackClient,
    channel: str,
    incoming_ts: str,
    thread_ts: str,
    user: str | None,
) -> dict[str, Any]:
    if parsed.used_dev_id:
        slack.post_message(
            channel,
            "Use the GitHub issue number (`9` or `#9`) for implement/test/…. "
            f"`DEV-N` works for `remove`, `status`, `cancel`, and `retry` while create has no issue yet.",
            thread_ts=incoming_ts or None,
        )
        raise SlackCommandError("dev id rejected")
    if parsed.issue_number is None:
        slack.post_message(
            channel,
            f"Include a GitHub issue number, e.g. `{parsed.command.value} 9`.",
            thread_ts=incoming_ts or None,
        )
        raise SlackCommandError("issue number required")

    phase = command_to_phase(parsed.command)
    if phase is None:
        raise SlackCommandError("unknown phase")
    if parsed.skip and phase == PipelinePhase.CREATE:
        slack.post_message(channel, "The create step cannot be skipped.", thread_ts=incoming_ts or None)
        raise SlackCommandError("cannot skip create")

    job = _resolve_issue_job(session, parsed, settings, slack, channel, incoming_ts, thread_ts, user)
    if job.status == JobStatus.RUNNING.value:
        slack.post_message(channel, f"{job.issue_ref} is already running `{job.pipeline_phase}`.", thread_ts=incoming_ts or None)
        raise SlackCommandError("job running")
    if job.status == JobStatus.COMPLETED.value:
        slack.post_message(channel, f"{job.issue_ref} is already completed.", thread_ts=incoming_ts or None)
        raise SlackCommandError("job completed")
    if job.status == JobStatus.CANCELLED.value:
        slack.post_message(channel, f"{job.issue_ref} is cancelled. Reply `retry {job.issue_ref.lstrip('#')}` first.", thread_ts=incoming_ts or None)
        raise SlackCommandError("job cancelled")

    allowed, reason = can_run_phase(job.phase_results, phase)
    if not allowed:
        slack.post_message(channel, f"{job.issue_ref}: {reason}", thread_ts=incoming_ts or None)
        raise SlackCommandError(reason or "phase gated")

    if parsed.skip:
        return _skip_phase(session, job, phase, slack, channel, incoming_ts)

    if phase in {PipelinePhase.TEST, PipelinePhase.SECURITY, PipelinePhase.ARCHITECT, PipelinePhase.DOCUMENT}:
        if not job.worktree_path and not job.pull_request_url and not job.branch_name:
            slack.post_message(
                channel,
                f"{job.issue_ref} has no implement branch yet. Run `@devbot implement {job.github_issue_number}` first "
                "(or skip implement only if a PR already exists).",
                thread_ts=incoming_ts or None,
            )
            raise SlackCommandError("implement required")

    job.pipeline_phase = phase.value
    job.current_stage = phase.value
    job.attempt = 0
    job.last_error = None
    job.completed_at = None
    release_lease(session, job)
    if job.slack_channel is None:
        job.slack_channel = channel
    if job.slack_thread_ts is None:
        job.slack_thread_ts = thread_ts
    if parsed.request:
        extra = parsed.request.strip()
        if extra:
            job.request = f"{job.request}\n\nFollow-up: {extra}"
    if job.status != JobStatus.QUEUED.value:
        transition_job(session, job, JobStatus.QUEUED, event_type=JobEventType.PHASE_STARTED, stage=phase.value, payload={"phase": phase.value})
    else:
        add_event(session, job, JobEventType.PHASE_STARTED, payload={"phase": phase.value})
    session.commit()
    slack.post_message(
        channel,
        format_progress(job, [f"→ `{phase.value}` queued for {job.issue_ref}", "Worker will claim this step."]),
        thread_ts=incoming_ts or job.slack_thread_ts,
    )
    return {"ok": True, "job_id": job.id, "command": parsed.command.value, "phase": phase.value}


def _handle_commit_command(
    session: Session,
    parsed,
    settings: Settings,
    slack: SlackClient,
    channel: str,
    incoming_ts: str,
    thread_ts: str,
    user: str | None,
) -> dict[str, Any]:
    if parsed.used_dev_id and parsed.issue_number is None and parsed.job_id is None:
        slack.post_message(
            channel,
            "Use a GitHub issue number for `commit`, e.g. `@devbot commit 9`.",
            thread_ts=incoming_ts or None,
        )
        raise SlackCommandError("dev id rejected")
    if parsed.issue_number is None and parsed.job_id is None:
        slack.post_message(channel, "Include a GitHub issue number, e.g. `commit 9`.", thread_ts=incoming_ts or None)
        raise SlackCommandError("issue number required")

    job = _resolve_issue_job(session, parsed, settings, slack, channel, incoming_ts, thread_ts, user)
    if job.status == JobStatus.RUNNING.value:
        slack.post_message(channel, f"{job.issue_ref} is already running. Wait for it to finish, then `@devbot commit {job.github_issue_number}`.", thread_ts=incoming_ts or None)
        raise SlackCommandError("job running")
    if not job.worktree_path and not job.branch_name and not job.pull_request_url:
        slack.post_message(
            channel,
            f"{job.issue_ref} has no worktree or branch to commit. Run `@devbot implement {job.github_issue_number}` first.",
            thread_ts=incoming_ts or None,
        )
        raise SlackCommandError("nothing to commit")

    job.specification = {
        **(job.specification or {}),
        "pending_action": "commit",
        "commit_resume_status": job.status,
    }
    job.attempt = 0
    job.last_error = None
    if job.slack_channel is None:
        job.slack_channel = channel
    if job.slack_thread_ts is None:
        job.slack_thread_ts = thread_ts
    release_lease(session, job)
    if job.status != JobStatus.QUEUED.value:
        transition_job(
            session,
            job,
            JobStatus.QUEUED,
            event_type=JobEventType.SLACK_REQUESTED,
            stage=job.current_stage or job.pipeline_phase,
            payload={"pending_action": "commit"},
        )
    else:
        add_event(session, job, JobEventType.SLACK_REQUESTED, payload={"pending_action": "commit"})
    session.commit()
    slack.post_message(
        channel,
        format_progress(job, ["→ Force-commit queued", "Worker will stage, commit if dirty, and push the branch."]),
        thread_ts=incoming_ts or job.slack_thread_ts,
    )
    return {"ok": True, "job_id": job.id, "command": "commit"}


def _skip_phase(session: Session, job: Job, phase: PipelinePhase, slack: SlackClient, channel: str, ts: str) -> dict[str, Any]:
    job.phase_results = mark_phase(job.phase_results, phase, PhaseStatus.SKIPPED, summary="Skipped in Slack")
    job.pipeline_phase = phase.value
    add_event(session, job, JobEventType.PHASE_SKIPPED, payload={"phase": phase.value})
    if job.status not in {JobStatus.IDLE.value, JobStatus.COMPLETED.value}:
        target = JobStatus.COMPLETED if next_phase(job.phase_results) is None else JobStatus.IDLE
        if job.status != target.value:
            transition_job(session, job, target, event_type=JobEventType.PHASE_SKIPPED, stage=phase.value)
    release_lease(session, job)
    session.commit()
    slack.post_message(
        channel,
        format_progress(job, [f"⏭ `{phase.value}` skipped for {job.issue_ref}", *next_action_lines(job)]),
        thread_ts=ts or job.slack_thread_ts,
    )
    return {"ok": True, "job_id": job.id, "skipped": phase.value}


def _resolve_issue_job(
    session: Session,
    parsed,
    settings: Settings,
    slack: SlackClient,
    channel: str,
    incoming_ts: str,
    thread_ts: str,
    user: str | None,
) -> Job:
    matches = find_jobs_by_issue(session, parsed.issue_number, parsed.repository)
    if len(matches) > 1:
        slack.post_message(
            channel,
            f"Issue #{parsed.issue_number} matches multiple repos: "
            + ", ".join(job.repository for job in matches)
            + ". Add the alias or `owner/name`.",
            thread_ts=incoming_ts or None,
        )
        raise SlackCommandError("ambiguous issue")
    if len(matches) == 1:
        return matches[0]
    if not parsed.repository:
        slack.post_message(
            channel,
            f"I do not have #{parsed.issue_number} yet. Add the repo alias or `owner/name`.",
            thread_ts=incoming_ts or None,
        )
        raise SlackCommandError("repository required to attach issue")
    if not _allowed_repo(settings, parsed.repository):
        slack.post_message(channel, f"Repository `{parsed.repository}` is not in ALLOWED_REPOS.", thread_ts=incoming_ts or None)
        raise SlackCommandError("repository not allowed")
    github = GitHubClient(settings)
    try:
        issue = github.get_issue(parsed.repository, parsed.issue_number)
    except Exception as exc:
        slack.post_message(
            channel,
            f"Could not load `{parsed.repository}#{parsed.issue_number}` from GitHub: {exc}",
            thread_ts=incoming_ts or None,
        )
        raise SlackCommandError("github issue missing") from exc
    spec = specification_from_issue(issue)
    results = empty_phase_results()
    results = mark_phase(results, PipelinePhase.CREATE, PhaseStatus.PASSED, summary="Attached existing GitHub issue")
    job, _ = create_job(
        session,
        external_key=f"github:{parsed.repository}#{parsed.issue_number}",
        repository=parsed.repository,
        request=parsed.request or spec.get("title") or issue.get("title") or f"Issue #{parsed.issue_number}",
        slack_channel=channel,
        slack_thread_ts=thread_ts,
        requested_by=user,
        pipeline_phase=command_to_phase(parsed.command) or PipelinePhase.IMPLEMENT,
        github_issue_number=parsed.issue_number,
        github_issue_url=issue.get("html_url"),
        phase_results=results,
        specification=spec,
    )
    attach_github_issue(job, parsed.issue_number, issue.get("html_url"))
    add_event(session, job, JobEventType.ISSUE_CREATED, payload={"imported": True, "number": parsed.issue_number})
    return job


def _resume_with_answers(session: Session, job: Job, answers: str, slack: SlackClient, channel: str, ts: str) -> dict[str, Any]:
    spec = dict(job.specification or {})
    prior = spec.get("answers") or []
    if isinstance(prior, list):
        prior = [*prior, answers]
    else:
        prior = [str(prior), answers]
    spec["answers"] = prior
    if _FORCE_CONTINUE_RE.search(answers):
        spec["force_continue"] = True
    job.specification = spec
    job.request = f"{job.request}\n\nClarification: {answers}"
    job.attempt = 0
    job.last_error = None
    release_lease(session, job)
    transition_job(session, job, JobStatus.QUEUED, event_type=JobEventType.SLACK_REQUESTED, payload={"answers": answers})
    session.commit()
    slack.post_message(
        channel,
        format_progress(job, ["✓ Thanks — I queued the same step with your answers."]),
        thread_ts=ts or job.slack_thread_ts,
    )
    return {"ok": True, "job_id": job.id, "command": "clarify"}


def _handle_control_command(
    session: Session, parsed, settings: Settings, slack: SlackClient, channel: str, ts: str
) -> dict[str, Any]:
    job = _resolve_control_job(session, parsed, slack, channel, ts)
    thread = ts or job.slack_thread_ts
    try:
        if parsed.command == SlackCommand.CANCEL:
            job.specification = {key: value for key, value in (job.specification or {}).items() if key != "autopilot"}
            if job.status not in {JobStatus.COMPLETED.value, JobStatus.CANCELLED.value}:
                transition_job(session, job, JobStatus.CANCELLED, event_type=JobEventType.CANCELLED)
            slack.post_message(channel, f"{job.issue_ref} cancelled.", thread_ts=thread)
        elif parsed.command == SlackCommand.REMOVE:
            notes = remove_job(session, job, settings, GitHubClient(settings))
            slack.post_message(channel, "\n".join(notes), thread_ts=thread)
            return {"ok": True, "command": "remove", "removed": True}
        elif parsed.command == SlackCommand.RETRY:
            job.attempt = 0
            job.last_error = None
            job.completed_at = None
            release_lease(session, job)
            if job.status != JobStatus.QUEUED.value:
                transition_job(session, job, JobStatus.QUEUED, event_type=JobEventType.RETRY_SCHEDULED)
            slack.post_message(channel, f"{job.issue_ref} queued for retry.", thread_ts=thread)
        elif parsed.command == SlackCommand.STATUS:
            slack.post_message(channel, format_job_status(job), thread_ts=thread)
    except InvalidTransition as exc:
        slack.post_message(channel, f"{job.issue_ref} cannot do that from {exc.current}.", thread_ts=thread)
        raise
    return {"ok": True, "job_id": job.id, "command": parsed.command.value}


def _resolve_control_job(session: Session, parsed, slack: SlackClient, channel: str, ts: str) -> Job:
    if parsed.job_id is not None:
        job = get_job(session, parsed.job_id)
        if job is None:
            slack.post_message(channel, f"I could not find `{parsed.command.value} DEV-{parsed.job_id}`.", thread_ts=ts or None)
            raise SlackCommandError("unknown job")
        return job
    if parsed.issue_number is None:
        slack.post_message(
            channel,
            "Include a GitHub issue number (`remove 42`) or an internal id (`remove DEV-2`) while create is still open.",
            thread_ts=ts or None,
        )
        raise SlackCommandError("issue number required")
    matches = find_jobs_by_issue(session, parsed.issue_number, parsed.repository)
    if not matches:
        slack.post_message(channel, f"I could not find #{parsed.issue_number}.", thread_ts=ts or None)
        raise SlackCommandError("unknown job")
    if len(matches) > 1:
        slack.post_message(channel, f"#{parsed.issue_number} is ambiguous; add the repo alias.", thread_ts=ts or None)
        raise SlackCommandError("ambiguous issue")
    return matches[0]


def format_job_created(job: Job) -> str:
    return (
        f"{job.repository}\n\n"
        f"Request: {job.request}\n\n"
        f"Phase: create\n"
        f"Internal id: `{job.display_id}` until the GitHub issue exists.\n"
        f"→ The coding agent will refine a GitHub issue. I will post `#N` and the title here when it is created.\n"
        + (
            "Autopilot is on. After create, implement → test → security → architect → document run without further Slack commands.\n"
            if (job.specification or {}).get("autopilot")
            else ""
        )
        + f"To drop this job: `@devbot remove {job.display_id}`"
    )


def format_progress(job: Job, lines: list[str]) -> str:
    heading = _job_heading(job)
    return f"{heading}\n\n" + "\n".join(lines)


def _job_heading(job: Job) -> str:
    ref = job.issue_ref if job.github_issue_number else f"{job.display_id} (no GitHub issue yet)"
    return f"{ref} — {job.issue_title}" if job.issue_title else ref


def format_job_status(job: Job) -> str:
    pr = f"\nPR: {job.pull_request_url}" if job.pull_request_url else ""
    issue = f"\nIssue: {job.github_issue_url}" if job.github_issue_url else ""
    err = f"\nLast error: {job.last_error}" if job.last_error else ""
    nxt = "\n" + "\n".join(next_action_lines(job))
    return (
        f"{_job_heading(job)}\n\n"
        f"Status: {job.status}\n"
        f"Phase: {job.pipeline_phase or 'n/a'}\n"
        f"Repository: {job.repository}\n"
        f"{_format_phase_line(job)}{issue}{pr}{nxt}{err}"
    )


def format_status_board(jobs: list[Job]) -> str:
    if not jobs:
        return "No pipeline jobs yet. Start with `@devbot create web …`."
    lines = ["Devbot pipeline\n"]
    for job in jobs:
        nxt = next_action_lines(job)[0]
        lines.append(
            f"{job.issue_ref}  {job.issue_title}  {job.repository}"
            + (f"  {job.credential_user}" if job.credential_user else "")
            + f"  {job.status}  {_format_phase_line(job)}  {nxt}"
        )
    return "\n".join(lines)


def _format_phase_line(job: Job) -> str:
    results = job.phase_results or {}
    parts = []
    for name, entry in results.items():
        status = (entry or {}).get("status")
        if status and status != PhaseStatus.PENDING.value:
            parts.append(f"{name} {status}")
    return " · ".join(parts) if parts else (job.pipeline_phase or "queued")
