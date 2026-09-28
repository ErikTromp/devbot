from __future__ import annotations

from pathlib import Path
from typing import Any

import markdown
import nh3

from app.agents.decision import is_plan_narration
from app.db.models import Job
from app.jobs.gates import next_phase, phase_entry, phase_is_done
from app.jobs.models import PIPELINE_ORDER, JobStatus, PipelinePhase, PhaseStatus

STATUS_COUNT_KEYS = (
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.IDLE.value,
    JobStatus.AWAITING_INPUT.value,
    JobStatus.FAILED.value,
    JobStatus.COMPLETED.value,
)

_EVENT_LABELS = {
    "SLACK_REQUESTED": "Slack request",
    "JOB_CREATED": "Job created",
    "JOB_CLAIMED": "Worker claimed job",
    "JOB_LEASE_RENEWED": "Lease renewed",
    "REFINEMENT_STARTED": "Refinement started",
    "REFINEMENT_COMPLETED": "Refinement completed",
    "NEED_INFO": "Waiting for input",
    "ISSUE_CREATED": "GitHub issue created",
    "ISSUE_UPDATED": "GitHub issue updated",
    "PHASE_STARTED": "Phase started",
    "PHASE_COMPLETED": "Phase completed",
    "PHASE_SKIPPED": "Phase skipped",
    "WORKTREE_CREATED": "Worktree created",
    "PLAN_STARTED": "Plan started",
    "PLAN_COMPLETED": "Plan completed",
    "CODING_STARTED": "Coding started",
    "CODING_COMPLETED": "Coding completed",
    "TESTS_STARTED": "Tests started",
    "TESTS_PASSED": "Tests passed",
    "TESTS_FAILED": "Tests failed",
    "TESTS_NOT_APPLICABLE": "Tests not applicable",
    "COMMIT_CREATED": "Commit created",
    "BRANCH_PUSHED": "Branch pushed",
    "PR_CREATED": "Pull request opened",
    "REVIEW_STARTED": "Review started",
    "REVIEW_FAILED": "Review failed",
    "FIX_REQUESTED": "Fix requested",
    "SECURITY_STARTED": "Security review started",
    "SECURITY_FAILED": "Security review failed",
    "DOCUMENTATION_STARTED": "Documentation started",
    "RETRY_SCHEDULED": "Retry scheduled",
    "COMPLETED": "Completed",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
    "PROJECT_SYNC_FAILED": "GitHub project sync failed",
}

_MD_EXTENSIONS = ("extra", "sane_lists", "fenced_code", "tables", "nl2br")
_MD_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_MD_ATTRIBUTES = {"a": {"href"}, "code": {"class"}, "th": {"align"}, "td": {"align"}}


def next_user_phase(job: Job) -> PipelinePhase | None:
    current = None
    if job.pipeline_phase:
        try:
            current = PipelinePhase(job.pipeline_phase)
        except ValueError:
            current = None
    if current and phase_is_done(phase_entry(job.phase_results, current)):
        idx = PIPELINE_ORDER.index(current)
        return PIPELINE_ORDER[idx + 1] if idx + 1 < len(PIPELINE_ORDER) else None
    return next_phase(job.phase_results)


def next_action_lines(job: Job) -> list[str]:
    nxt = next_user_phase(job)
    if not nxt:
        return ["Pipeline complete. Review and merge the PR yourself."]
    if not job.github_issue_number:
        return [f"Next: `{nxt.value}` (waiting for a GitHub issue number)."]
    n = job.github_issue_number
    if (job.specification or {}).get("autopilot") and job.status == JobStatus.QUEUED.value:
        return [f"Autopilot queued `{nxt.value}` for #{n}."]
    run = f"`@devbot {nxt.value} {n}`"
    if nxt == PipelinePhase.CREATE:
        return [f"Next: run {run}"]
    return [f"Next: run {run} or skip with `@devbot {nxt.value} skip {n}`"]


_ACTIVITY = {
    "create": "Writing the GitHub issue",
    "implement": "Starting implement",
    "test": "Running the test step",
    "security": "Running the security review",
    "architect": "Running the architecture review",
    "document": "Writing documentation",
    "worktree": "Creating the worktree",
    "planning": "Writing the implementation plan",
    "coding": "Implementing the plan",
    "testing": "Running tests",
    "committing": "Committing",
    "pushing": "Pushing the branch",
    "creating_pr": "Opening the pull request",
    "force_commit": "Force-committing and pushing",
}


def activity_label(job: Job) -> str:
    if job.status != JobStatus.RUNNING.value:
        return ""
    stage = job.current_stage or job.pipeline_phase or ""
    label = _ACTIVITY.get(stage) or f"Running {stage.replace('_', ' ')}"
    if job.attempt:
        return f"{label} (attempt {job.attempt + 1})"
    return label


def display_phase_results(job: Job) -> dict[str, Any]:
    """Copy phase results and mark the active phase running for the dashboard."""
    results = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in (job.phase_results or {}).items()
    }
    phase = job.pipeline_phase
    if job.status != JobStatus.RUNNING.value or not phase:
        return results
    entry = dict(results.get(phase) or {})
    if entry.get("status") in {None, "", PhaseStatus.PENDING.value}:
        entry["status"] = PhaseStatus.RUNNING.value
        entry["summary"] = activity_label(job)
        results[phase] = entry
    return results


def board_phase(job: Job) -> str:
    current = None
    if job.pipeline_phase:
        try:
            current = PipelinePhase(job.pipeline_phase)
        except ValueError:
            current = None
    if job.status == JobStatus.COMPLETED.value:
        return PipelinePhase.DOCUMENT.value
    if job.status in {
        JobStatus.RUNNING.value,
        JobStatus.FAILED.value,
        JobStatus.AWAITING_INPUT.value,
    }:
        return (current or PipelinePhase.CREATE).value
    nxt = next_user_phase(job)
    return nxt.value if nxt else PipelinePhase.DOCUMENT.value


def specification_subset(job: Job) -> dict[str, Any]:
    spec = job.specification or {}
    return {
        "title": spec.get("title"),
        "implementation_plan_path": spec.get("implementation_plan_path"),
        "implementation_plan_summary": spec.get("implementation_plan_summary"),
    }


def status_counts(jobs: list[Job]) -> dict[str, int]:
    counts = {key: 0 for key in STATUS_COUNT_KEYS}
    for job in jobs:
        if job.status in counts:
            counts[job.status] += 1
    return counts


def read_job_plan(job: Job) -> dict[str, Any]:
    spec = job.specification or {}
    relative = str(spec.get("implementation_plan_path") or ".devbot/plan.md")
    summary = str(spec.get("implementation_plan_summary") or "").strip()
    stored = str(spec.get("implementation_plan") or "").strip()
    markdown = ""
    source = "missing"
    if job.worktree_path:
        path = Path(job.worktree_path) / relative
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                if text.strip() and not is_plan_narration(text):
                    markdown = text
                    source = "worktree"
        except OSError:
            markdown = ""
    if not markdown and stored and not is_plan_narration(stored):
        markdown = stored
        source = "stored"
    if not markdown and summary and not is_plan_narration(summary):
        markdown = summary
        source = "summary"
    return {
        "path": relative,
        "markdown": markdown,
        "summary": summary,
        "source": source,
        "html": render_plan_markdown(markdown) if markdown else "",
    }


def event_label(event_type: str) -> str:
    return _EVENT_LABELS.get(event_type, event_type.replace("_", " ").title())


def render_plan_markdown(text: str) -> str:
    if not text.strip():
        return ""
    raw = markdown.markdown(text.replace("\r\n", "\n"), extensions=list(_MD_EXTENSIONS))
    return nh3.clean(raw, tags=_MD_TAGS, attributes=_MD_ATTRIBUTES, link_rel="noreferrer")
