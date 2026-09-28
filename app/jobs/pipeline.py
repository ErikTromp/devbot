from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.architecture import ArchitectureAgent
from app.agents.base import AgentContext, AgentResult
from app.agents.coding import CodingAgent
from app.agents.decision import is_plan_narration, needs_more_info, parse_agent_decision, question_lines
from app.agents.documentation import DocumentationAgent
from app.agents.elaborate import ElaborateTestAgent
from app.agents.refinement import RefinementAgent
from app.agents.security import SecurityAgent
from app.agents.testing import TestAgent
from app.config import Settings
from app.db.models import Job
from app.git.repository import GitError, GitRepository, ensure_repo_cache
from app.git.worktree import create_worktree, remove_worktree, worktree_path_for
from app.github.client import GitHubClient
from app.github.issues import issue_body
from app.github.projects import ProjectSyncError
from app.github.pr import build_pr_body, pr_title
from app.jobs.gates import mark_phase, next_phase, next_retry, phase_entry, phase_is_done
from app.jobs.models import CheckResult, JobEventType, JobStage, JobStatus, PhaseStatus, PipelinePhase
from app.jobs.service import add_event, attach_github_issue, release_lease, renew_lease, transition_job
from app.jobs.ticket_context import gather_ticket_context
from app.logging import log_extra
from app.security.secrets import safe_error
from app.slack.client import SlackClient
from app.slack.events import format_progress, next_action_lines

logger = logging.getLogger(__name__)


@dataclass
class WorkerDeps:
    settings: Settings
    slack: SlackClient
    github: GitHubClient
    coding: CodingAgent
    testing: TestAgent
    refinement: RefinementAgent | None = None
    elaborate: ElaborateTestAgent | None = None
    security: SecurityAgent | None = None
    architecture: ArchitectureAgent | None = None
    documentation: DocumentationAgent | None = None


class PipelineError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class NeedInfoError(Exception):
    def __init__(self, questions: list[str]) -> None:
        super().__init__("Cursor needs more information")
        self.questions = questions


def _agent_context(job: Job, worktree: Path, deps: WorkerDeps, *, mode: str = "agent") -> AgentContext:
    return AgentContext(
        job=job,
        worktree=worktree,
        prompt="",
        extra={"mode": mode, "conversation": gather_ticket_context(job, slack=deps.slack, github=deps.github)},
    )


def notify(job: Job, slack: SlackClient, lines: list[str]) -> None:
    if job.slack_channel:
        slack.post_message(job.slack_channel, format_progress(job, lines), thread_ts=job.slack_thread_ts)


def run_claimed_job(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    extra = log_extra(job_id=job.id, repository=job.repository, attempt=job.attempt)
    logger.info("job_started", extra=extra)
    try:
        if job.status == JobStatus.QUEUED.value:
            keep_stage = job.current_stage in _IMPLEMENT_RESUME_STAGES or (job.specification or {}).get("pending_action") == "commit"
            transition_job(
                session,
                job,
                JobStatus.RUNNING,
                event_type=JobEventType.PHASE_STARTED,
                stage=None if keep_stage else job.pipeline_phase,
            )
            session.commit()
        if (job.specification or {}).get("pending_action") == "commit":
            _run_force_commit(session, job, deps, worker_id)
            logger.info("job_completed", extra=extra)
            return
        phase = PipelinePhase(job.pipeline_phase or PipelinePhase.CREATE.value)
        if phase == PipelinePhase.CREATE:
            _run_create(session, job, deps, worker_id)
        elif phase == PipelinePhase.IMPLEMENT:
            _run_implement(session, job, deps, worker_id)
        elif phase == PipelinePhase.TEST:
            _run_cursor_phase(session, job, deps, worker_id, phase, deps.elaborate, JobEventType.TESTS_STARTED)
        elif phase == PipelinePhase.SECURITY:
            _run_cursor_phase(session, job, deps, worker_id, phase, deps.security, JobEventType.SECURITY_STARTED)
        elif phase == PipelinePhase.ARCHITECT:
            _run_cursor_phase(session, job, deps, worker_id, phase, deps.architecture, JobEventType.REVIEW_STARTED)
        elif phase == PipelinePhase.DOCUMENT:
            _run_cursor_phase(session, job, deps, worker_id, phase, deps.documentation, JobEventType.DOCUMENTATION_STARTED)
        else:
            raise PipelineError(f"Unknown phase {phase}", retryable=False)
        logger.info("job_completed", extra=extra)
    except NeedInfoError as exc:
        job_id = job.id
        session.rollback()
        job = session.get(Job, job_id)
        if job is None:
            raise
        _await_input(session, job, deps, exc.questions)
    except Exception as exc:
        job_id = job.id
        session.rollback()
        job = session.get(Job, job_id)
        if job is None:
            raise
        _fail_or_retry(session, job, deps, exc)


def _worktree_repo(job: Job, settings: Settings) -> GitRepository:
    path = Path(job.worktree_path) if job.worktree_path else worktree_path_for(settings, job.id)
    return GitRepository(path, settings)


def _issue_node_id(job: Job, deps: WorkerDeps) -> str:
    stored = str((job.specification or {}).get("github_issue_node_id") or "")
    if stored:
        return stored
    issue = deps.github.get_issue(job.repository, job.github_issue_number)
    return str(issue.get("node_id") or "")


def _sync_project_board(session: Session, job: Job, deps: WorkerDeps, status: str) -> None:
    if not job.github_issue_number:
        return
    sync = getattr(deps.github, "sync_pipeline_project", None)
    if sync is None:
        return
    try:
        node_id = _issue_node_id(job, deps)
        if not node_id:
            return
        spec = job.specification or {}
        ids = sync(
            issue_node_id=node_id,
            status=status,
            pinned_project_id=deps.settings.github_project_id,
            stored_project_id=str(spec.get("github_project_id") or ""),
            stored_item_id=str(spec.get("github_project_item_id") or ""),
            owner=job.credential_user or "",
        )
        job.specification = {
            **spec,
            "github_issue_node_id": node_id,
            **{key: value for key, value in (ids or {}).items() if value},
        }
    except ProjectSyncError as exc:
        logger.warning(
            "github_project_sync_failed",
            extra=log_extra(job_id=job.id, repository=job.repository, stage=str(exc)),
        )
        add_event(session, job, JobEventType.PROJECT_SYNC_FAILED, payload={"error": str(exc)[:500], "status": status})
    except Exception:
        logger.warning("github_project_sync_failed", extra=log_extra(job_id=job.id, repository=job.repository))
        add_event(session, job, JobEventType.PROJECT_SYNC_FAILED, payload={"status": status})


def _github_touch(job: Job, deps: WorkerDeps, phase: PipelinePhase, comment: str, session: Session | None = None) -> None:
    if not job.github_issue_number:
        return
    try:
        deps.github.comment_on_issue(job.repository, job.github_issue_number, comment)
        deps.github.set_pipeline_label(job.repository, job.github_issue_number, phase.value)
        deps.github.assign_issue(job.repository, job.github_issue_number)
    except Exception:
        logger.warning("github_issue_update_failed", extra=log_extra(job_id=job.id, repository=job.repository))
    if session is not None:
        _sync_project_board(session, job, deps, phase.value)


def _should_bypass_need_info(job: Job) -> bool:
    spec = job.specification or {}
    if spec.get("force_continue"):
        return True
    if job.pipeline_phase == PipelinePhase.CREATE.value and spec.get("answers"):
        return True
    return False


def _raise_if_need_info(result: AgentResult, job: Job) -> dict:
    decision = parse_agent_decision(result.summary, result.structured) or (result.structured or {})
    if _should_bypass_need_info(job):
        return decision
    if needs_more_info(decision):
        questions = question_lines(decision)
        raise NeedInfoError(questions or ["The ticket needs more detail before I can continue."])
    return decision


def _run_create(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    if job.github_issue_number:
        _finish_phase(
            session,
            job,
            deps,
            PipelinePhase.CREATE,
            "Issue already exists",
            extra_lines=[f"Ticket {job.issue_ref}: {job.issue_title}"],
        )
        return
    if deps.refinement is None:
        raise PipelineError("Refinement agent is not configured", retryable=False)
    renew_lease(session, job, worker_id, deps.settings)
    add_event(session, job, JobEventType.REFINEMENT_STARTED)
    session.commit()
    notify(job, deps.slack, ["→ Refining a GitHub issue with Cursor (agent mode)"])
    cache = ensure_repo_cache(deps.settings, job.repository)
    result = deps.refinement.run(_agent_context(job, cache.path, deps, mode="agent"))
    if not result.ok:
        raise PipelineError(result.summary or "Refinement agent failed", retryable=True)
    decision = _raise_if_need_info(result, job)
    title = str(decision.get("title") or job.request.strip().split("\n", 1)[0])[:80]
    criteria = [str(item) for item in (decision.get("acceptance_criteria") or [])]
    body = str(decision.get("body") or "")
    issue = deps.github.create_issue(
        job.repository,
        title=title,
        body=issue_body(title=title, description=body or job.request, acceptance_criteria=criteria),
        labels=["devbot:create"],
    )
    attach_github_issue(job, int(issue["number"]), issue.get("html_url"))
    job.specification = {
        **(job.specification or {}),
        "title": title,
        "body": body,
        "acceptance_criteria": criteria,
        "github_issue_node_id": issue.get("node_id"),
    }
    add_event(session, job, JobEventType.ISSUE_CREATED, payload={"number": job.github_issue_number, "url": job.github_issue_url})
    session.commit()
    _github_touch(
        job,
        deps,
        PipelinePhase.CREATE,
        f"**create** finished at {datetime.now(UTC).isoformat()} — ticket is ready. Next: `@devbot implement {job.github_issue_number}`",
        session,
    )
    _finish_phase(
        session,
        job,
        deps,
        PipelinePhase.CREATE,
        f"Created GitHub issue {job.issue_ref}",
        extra_lines=[
            f"Created ticket {job.issue_ref}: {job.issue_title}",
            f"Issue: {job.github_issue_url}",
        ],
    )


def _run_implement(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    resuming = job.current_stage in _IMPLEMENT_RESUME_STAGES
    _github_touch(job, deps, PipelinePhase.IMPLEMENT, f"**implement started** at {datetime.now(UTC).isoformat()}", session)
    if resuming:
        notify(job, deps.slack, ["→ Resuming implement from the existing diff"])
    else:
        notify(job, deps.slack, ["→ Creating isolated worktree", "→ Plan mode, then agent implements the plan"])
    _ensure_worktree(session, job, deps, worker_id)
    _commit_and_push(session, job, deps, worker_id, reason="publish branch")
    _run_plan(session, job, deps, worker_id)
    repo = _worktree_repo(job, deps.settings)
    repo.ensure_devbot_plan_excluded()
    implement_base = repo.head_sha()
    _run_coding(session, job, deps, worker_id)
    _run_tests(session, job, deps, worker_id)
    diff = repo.diff()
    _commit_and_push(session, job, deps, worker_id, reason="implement", baseline_sha=implement_base)
    _create_or_update_pr(session, job, deps, worker_id, diff_stat=diff.stdout)
    _github_touch(
        job,
        deps,
        PipelinePhase.IMPLEMENT,
        f"**implement finished** at {datetime.now(UTC).isoformat()}"
        + (f"\nPR: {job.pull_request_url}" if job.pull_request_url else ""),
        session,
    )
    _finish_phase(
        session,
        job,
        deps,
        PipelinePhase.IMPLEMENT,
        "Implementation complete",
        extra_lines=[
            f"PR: {job.pull_request_url}" if job.pull_request_url else "PR updated",
        ],
    )


def _run_cursor_phase(
    session: Session,
    job: Job,
    deps: WorkerDeps,
    worker_id: str,
    phase: PipelinePhase,
    agent,
    event_type: JobEventType,
) -> None:
    if agent is None:
        raise PipelineError(f"{phase.value} agent is not configured", retryable=False)
    if not job.worktree_path or not Path(job.worktree_path).exists():
        if job.branch_name:
            _ensure_worktree(session, job, deps, worker_id)
        else:
            raise PipelineError("No implement worktree or branch. Run implement first.", retryable=False)
    _github_touch(job, deps, phase, f"**{phase.value} started** at {datetime.now(UTC).isoformat()}", session)
    notify(job, deps.slack, [f"→ Running `{phase.value}`"])
    renew_lease(session, job, worker_id, deps.settings)
    add_event(session, job, event_type, payload={"phase": phase.value})
    session.commit()
    _commit_and_push(session, job, deps, worker_id, reason=f"publish before {phase.value}")
    repo = _worktree_repo(job, deps.settings)
    repo.ensure_devbot_plan_excluded()
    baseline_sha = repo.head_sha()
    result = agent.run(_agent_context(job, Path(job.worktree_path or "."), deps, mode="agent"))
    decision = _raise_if_need_info(result, job)
    status = str(decision.get("status") or "").upper()
    if not result.ok or status in {CheckResult.FAIL.value, CheckResult.BLOCKED.value}:
        findings = decision.get("findings") or result.summary
        raise PipelineError(result.summary or f"{phase.value} failed: {findings}", retryable=status != CheckResult.BLOCKED.value)
    if status == CheckResult.NOT_APPLICABLE.value and phase == PipelinePhase.TEST:
        raise PipelineError(result.summary or "Elaborate tests were not applicable; not treating as a pass.", retryable=False)
    _commit_and_push(session, job, deps, worker_id, reason=phase.value, baseline_sha=baseline_sha)
    _github_touch(
        job,
        deps,
        phase,
        f"**{phase.value} finished** at {datetime.now(UTC).isoformat()}\n\n{result.summary[:1500]}",
        session,
    )
    extra = [f"PR: {job.pull_request_url}"] if job.pull_request_url else []
    _finish_phase(session, job, deps, phase, result.summary or f"{phase.value} complete", extra_lines=extra)


def _ensure_worktree(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    published = str((job.specification or {}).get("pushed_sha") or "")
    existing = Path(job.worktree_path) if job.worktree_path else None
    if existing and existing.exists() and (existing / ".git").exists():
        if not published or _worktree_repo(job, deps.settings).contains_commit(published):
            return
        notify(
            job,
            deps.slack,
            [
                f"Worktree is missing pushed commit `{published[:12]}`.",
                "Recreating it from origin instead of keeping a checkout that dropped that work.",
            ],
        )
        remove_worktree(ensure_repo_cache(deps.settings, job.repository), existing, keep_metadata=False)
        job.worktree_path = None
    renew_lease(session, job, worker_id, deps.settings)
    cache = ensure_repo_cache(deps.settings, job.repository)
    branch = job.branch_name or f"agent/issue-{job.github_issue_number or job.id}"
    try:
        path, how = create_worktree(
            cache,
            deps.settings,
            job.id,
            branch,
            allow_new_branch=not _branch_already_published(job),
        )
    except GitError as exc:
        raise PipelineError(str(exc), retryable=False) from exc
    job.worktree_path = str(path)
    job.branch_name = branch
    job.current_stage = JobStage.WORKTREE.value
    add_event(session, job, JobEventType.WORKTREE_CREATED, payload={"path": str(path), "branch": branch, "how": how})
    session.commit()
    _worktree_repo(job, deps.settings).ensure_devbot_plan_excluded()
    if how == "reused":
        return
    if how == "reattached":
        notify(job, deps.slack, [f"✓ Reattached worktree to existing `{branch}` (same ticket branch)"])
    elif how == "from_remote":
        notify(job, deps.slack, [f"✓ Recreated worktree from `origin/{branch}`"])
    else:
        notify(
            job,
            deps.slack,
            [
                f"✓ Created new worktree `{branch}` from `{deps.settings.default_github_branch}`",
                "No prior ticket branch was found locally or on origin — this checkout does not include earlier unpushed work.",
            ],
        )


_IMPLEMENT_RESUME_STAGES = {
    JobStage.TESTING.value,
    JobStage.COMMITTING.value,
    JobStage.PUSHING.value,
    JobStage.CREATING_PR.value,
}


def _run_plan(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    if job.pull_request_url and job.current_stage == JobStage.CREATING_PR.value:
        return
    if _reuse_existing_implementation(job, deps):
        notify(job, deps.slack, ["→ Reusing the existing plan and worktree (not starting over)"])
        return
    planner = getattr(deps.coding, "plan", None)
    if planner is None:
        return
    renew_lease(session, job, worker_id, deps.settings)
    job.current_stage = JobStage.PLANNING.value
    add_event(session, job, JobEventType.PLAN_STARTED, payload={"attempt": job.attempt})
    session.commit()
    logger.info("job_activity", extra=log_extra(job_id=job.id, repository=job.repository, stage=job.current_stage, attempt=job.attempt))
    notify(job, deps.slack, ["→ Writing an implementation plan (Cursor plan mode)"])
    result = planner(_agent_context(job, Path(job.worktree_path or "."), deps, mode="plan"))
    if not result.ok:
        raise PipelineError(result.summary or "Planning agent failed", retryable=True)
    decision = _raise_if_need_info(result, job)
    plan = _select_plan(decision, result, job)
    if not plan:
        raise PipelineError("Cursor returned a status update instead of an implementation plan.", retryable=True)
    plan_path = _write_plan_file(job, plan)
    lines = plan.count("\n") + 1
    words = len(plan.split())
    job.specification = {
        **(job.specification or {}),
        "implementation_plan": plan,
        "implementation_plan_path": plan_path,
        "implementation_plan_summary": _plan_summary(plan),
    }
    add_event(session, job, JobEventType.PLAN_COMPLETED, payload={"path": plan_path, "lines": lines, "words": words})
    session.commit()
    ticket_url = deps.settings.ticket_url(job.display_id)
    ready = [f"✓ Plan ready ({lines} lines, ~{words} words)."]
    if ticket_url:
        ready.append(ticket_url)
    ready.append(f"Plan saved locally at `{plan_path}` (never committed; too long for Slack).")
    notify(job, deps.slack, ready)
    _github_touch(
        job,
        deps,
        PipelinePhase.IMPLEMENT,
        f"**implement:** plan written to `{plan_path}` ({lines} lines). Implementing next. Review it on the PR, not in this comment.",
        session,
    )


def _select_plan(decision: dict, result: AgentResult, job: Job) -> str:
    structured = result.structured or {}
    for raw in (structured.get("plan"), decision.get("plan")):
        text = str(raw or "").strip()
        if text and not is_plan_narration(text):
            return text
    if job.worktree_path:
        path = Path(job.worktree_path) / ".devbot" / "plan.md"
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text and not is_plan_narration(text):
                    return text
        except OSError:
            return ""
    return ""


def _write_plan_file(job: Job, plan: str) -> str:
    relative = ".devbot/plan.md"
    if not job.worktree_path:
        return relative
    path = Path(job.worktree_path) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.rstrip() + "\n", encoding="utf-8")
    return relative


def _plan_summary(plan: str, *, limit: int = 280) -> str:
    text = " ".join(plan.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _reuse_existing_implementation(job: Job, deps: WorkerDeps) -> bool:
    if job.current_stage not in _IMPLEMENT_RESUME_STAGES:
        return False
    if (job.specification or {}).get("implementation_plan_path") or (
        job.worktree_path and (Path(job.worktree_path) / ".devbot" / "plan.md").exists()
    ):
        repo = _worktree_repo(job, deps.settings)
        return repo.has_changes() or bool(job.pull_request_url)
    return False


def _run_coding(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    if job.pull_request_url and job.current_stage == JobStage.CREATING_PR.value:
        return
    if _reuse_existing_implementation(job, deps):
        return
    renew_lease(session, job, worker_id, deps.settings)
    job.current_stage = JobStage.CODING.value
    add_event(session, job, JobEventType.CODING_STARTED, payload={"attempt": job.attempt})
    session.commit()
    logger.info("job_activity", extra=log_extra(job_id=job.id, repository=job.repository, stage=job.current_stage, attempt=job.attempt))
    notify(job, deps.slack, ["→ Implementing the plan (Cursor agent mode)"])
    result = deps.coding.run(_agent_context(job, Path(job.worktree_path or "."), deps, mode="agent"))
    decision = _raise_if_need_info(result, job)
    add_event(
        session,
        job,
        JobEventType.CODING_COMPLETED if result.ok else JobEventType.FAILED,
        payload={
            "ok": result.ok,
            "exit_code": result.exit_code,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.metadata.get("timed_out"),
            "summary": result.summary[:1000],
        },
    )
    session.commit()
    if not result.ok:
        raise PipelineError(result.summary or "Cursor agent failed", retryable=True)
    if decision.get("acceptance_criteria_met") is False:
        unmet = decision.get("unmet") or []
        raise PipelineError("Acceptance criteria not met: " + ", ".join(str(item) for item in unmet), retryable=True)
    repo = _worktree_repo(job, deps.settings)
    coding_base = repo.head_sha()
    if not repo.has_committable_changes() and repo.head_sha() == coding_base:
        raise PipelineError("Cursor reported completion but the worktree has no file changes.", retryable=True)
    job.current_stage = JobStage.TESTING.value
    session.commit()
    notify(job, deps.slack, ["✓ Implementation complete (diff present)", "→ Running tests"])


def _run_tests(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    if job.pull_request_url and job.current_stage == JobStage.CREATING_PR.value:
        return
    renew_lease(session, job, worker_id, deps.settings)
    job.current_stage = JobStage.TESTING.value
    add_event(session, job, JobEventType.TESTS_STARTED)
    session.commit()
    logger.info("job_activity", extra=log_extra(job_id=job.id, repository=job.repository, stage=job.current_stage, attempt=job.attempt))
    result = deps.testing.run(AgentContext(job=job, worktree=Path(job.worktree_path or "."), prompt=""))
    status = (result.structured or {}).get("status")
    if status == CheckResult.NOT_APPLICABLE.value:
        add_event(session, job, JobEventType.TESTS_NOT_APPLICABLE, payload={"summary": result.summary})
        job.current_stage = JobStage.COMMITTING.value
        session.commit()
        notify(
            job,
            deps.slack,
            [
                f"⚠ {result.summary}",
                "Continuing to the PR. Broader/Playwright coverage is `@devbot test "
                f"{job.github_issue_number or job.issue_ref}`.",
            ],
        )
        return
    if status == CheckResult.BLOCKED.value:
        add_event(session, job, JobEventType.TESTS_FAILED, payload={"summary": result.summary, "stderr": (result.stderr or "")[-1500:]})
        job.current_stage = JobStage.COMMITTING.value
        session.commit()
        notify(
            job,
            deps.slack,
            [
                f"⚠ Tests could not start: {result.summary}",
                "Continuing to the PR with the existing diff.",
                (result.stderr or result.stdout)[-400:],
            ],
        )
        return
    if not result.ok:
        add_event(session, job, JobEventType.TESTS_FAILED, payload={"stdout": result.stdout[-1500:], "stderr": result.stderr[-1500:]})
        session.commit()
        notify(job, deps.slack, [f"✗ {result.summary}", (result.stderr or result.stdout)[-400:]])
        raise PipelineError(result.summary or "Tests failed", retryable=True)
    add_event(session, job, JobEventType.TESTS_PASSED, payload={"tests_run": (result.structured or {}).get("tests_run")})
    job.current_stage = JobStage.COMMITTING.value
    session.commit()
    notify(job, deps.slack, ["✓ Tests passed", "→ Creating or updating PR"])


def _create_or_update_pr(session: Session, job: Job, deps: WorkerDeps, worker_id: str, *, diff_stat: str = "") -> None:
    renew_lease(session, job, worker_id, deps.settings)
    test_summary = "Independent unit/package tests ran. Missing runners fail the implement step."
    body = build_pr_body(
        job,
        diff_stat=diff_stat,
        test_summary=test_summary,
        agent_verification="Independent git diff confirmed worktree changes. Cursor success text was not trusted.",
    )
    if job.pull_request_url and job.pull_request_number:
        try:
            deps.github.update_pull_request(job.repository, job.pull_request_number, body=body)
        except Exception:
            logger.warning("github_pr_update_failed", extra=log_extra(job_id=job.id))
        return
    branch = job.branch_name or f"agent/issue-{job.github_issue_number or job.id}"
    pr = deps.github.create_pull_request(
        job.repository,
        title=pr_title(job),
        body=body,
        head=branch,
        base=deps.settings.default_github_branch,
    )
    job.pull_request_number = pr.get("number")
    job.pull_request_url = pr.get("html_url")
    add_event(session, job, JobEventType.PR_CREATED, payload={"number": job.pull_request_number, "url": job.pull_request_url})
    job.current_stage = JobStage.CREATING_PR.value
    session.commit()


def _branch_already_published(job: Job) -> bool:
    spec = job.specification or {}
    if spec.get("pushed_sha") or job.pull_request_url:
        return True
    return phase_is_done(phase_entry(job.phase_results, PipelinePhase.IMPLEMENT))


def _autopilot(job: Job) -> bool:
    return bool((job.specification or {}).get("autopilot"))


def _commit_and_push(
    session: Session,
    job: Job,
    deps: WorkerDeps,
    worker_id: str,
    *,
    reason: str,
    baseline_sha: str | None = None,
) -> dict[str, bool]:
    """Commit this step's file changes (never `.devbot/plan.md`) and always push."""
    if not job.worktree_path or not Path(job.worktree_path).exists():
        raise PipelineError(
            "No worktree to commit and push. This step will not finish until the branch is on origin.",
            retryable=False,
        )
    renew_lease(session, job, worker_id, deps.settings)
    repo = _worktree_repo(job, deps.settings)
    repo.configure_identity()
    repo.ensure_devbot_plan_excluded()
    branch = job.branch_name or f"agent/issue-{job.github_issue_number or job.id}"
    repo.ensure_head_branch(branch)
    if baseline_sha and repo.head_sha() != baseline_sha:
        repo.soft_reset(baseline_sha)
    committed = False
    if repo.has_committable_changes() or repo.has_staged_changes():
        job.current_stage = JobStage.COMMITTING.value
        session.commit()
        repo.commit(f"{job.issue_ref}: {reason}")
        committed = True
        add_event(session, job, JobEventType.COMMIT_CREATED, payload={"phase": reason, "branch": branch})
        session.commit()
    job.current_stage = JobStage.PUSHING.value
    session.commit()
    repo.push(branch)
    pushed_sha = repo.head_sha()
    job.specification = {**(job.specification or {}), "pushed_sha": pushed_sha, "pushed_branch": branch}
    add_event(session, job, JobEventType.BRANCH_PUSHED, payload={"branch": branch, "committed": committed, "sha": pushed_sha})
    session.commit()
    lines = []
    if committed:
        lines.append(f"✓ Committed `{reason}` changes")
    else:
        lines.append(f"No committable changes for `{reason}` (`.devbot/plan.md` is never committed)")
    lines.append(f"✓ Pushed `{branch}`")
    if job.pull_request_url:
        lines.append(f"PR: {job.pull_request_url}")
    notify(job, deps.slack, lines)
    return {"committed": committed, "pushed": True}


def _run_force_commit(session: Session, job: Job, deps: WorkerDeps, worker_id: str) -> None:
    resume = str((job.specification or {}).get("commit_resume_status") or JobStatus.IDLE.value)
    notify(job, deps.slack, ["→ Force-commit: stage, commit if dirty, push"])
    _ensure_worktree(session, job, deps, worker_id)
    result = _commit_and_push(session, job, deps, worker_id, reason="manual commit")
    spec = {key: value for key, value in (job.specification or {}).items() if key not in {"pending_action", "commit_resume_status"}}
    job.specification = spec
    if resume == JobStatus.COMPLETED.value or next_phase(job.phase_results) is None:
        target = JobStatus.COMPLETED
    else:
        target = JobStatus.IDLE
    if job.status != target.value:
        transition_job(
            session,
            job,
            target,
            event_type=JobEventType.PHASE_COMPLETED,
            stage=job.pipeline_phase or JobStage.COMMITTING.value,
            payload={"pending_action": "commit", **result},
        )
    release_lease(session, job)
    session.commit()
    extra = [f"PR: {job.pull_request_url}"] if job.pull_request_url else []
    if not result["committed"] and result["pushed"]:
        extra.insert(0, "Nothing to commit (verification-only step or no file edits). Branch pushed.")
    notify(job, deps.slack, ["✓ Force-commit finished", *extra, *next_action_lines(job)])


def _finish_phase(session: Session, job: Job, deps: WorkerDeps, phase: PipelinePhase, summary: str, extra_lines: list[str] | None = None) -> None:
    job.phase_results = mark_phase(job.phase_results, phase, PhaseStatus.PASSED, summary=summary[:1000])
    job.pipeline_phase = phase.value
    nxt = next_phase(job.phase_results) if _autopilot(job) else None
    if nxt is not None:
        _queue_autopilot_phase(session, job, nxt)
        session.commit()
        notify(job, deps.slack, [f"✓ `{phase.value}` complete", *list(extra_lines or []), *next_action_lines(job)])
        return
    target = JobStatus.COMPLETED if phase == PipelinePhase.DOCUMENT or next_phase(job.phase_results) is None else JobStatus.IDLE
    if target == JobStatus.COMPLETED:
        _sync_project_board(session, job, deps, "done")
    if job.status != target.value:
        transition_job(session, job, target, event_type=JobEventType.PHASE_COMPLETED, stage=phase.value, payload={"phase": phase.value})
    release_lease(session, job)
    session.commit()
    lines = [f"✓ `{phase.value}` complete", *list(extra_lines or []), *next_action_lines(job)]
    notify(job, deps.slack, lines)
    if target == JobStatus.COMPLETED:
        _maybe_cleanup(job, deps)


def _queue_autopilot_phase(session: Session, job: Job, phase: PipelinePhase) -> None:
    job.pipeline_phase = phase.value
    job.current_stage = phase.value
    job.attempt = 0
    job.last_error = None
    release_lease(session, job)
    transition_job(
        session,
        job,
        JobStatus.QUEUED,
        event_type=JobEventType.PHASE_STARTED,
        stage=phase.value,
        payload={"phase": phase.value, "autopilot": True},
    )


def _await_input(session: Session, job: Job, deps: WorkerDeps, questions: list[str]) -> None:
    job.phase_results = mark_phase(
        job.phase_results,
        PipelinePhase(job.pipeline_phase or PipelinePhase.CREATE.value),
        PhaseStatus.AWAITING_INPUT,
        summary="; ".join(questions)[:1000],
    )
    if job.status != JobStatus.AWAITING_INPUT.value:
        transition_job(session, job, JobStatus.AWAITING_INPUT, event_type=JobEventType.NEED_INFO, payload={"questions": questions})
    release_lease(session, job)
    session.commit()
    numbered = "\n".join(f"{idx}. {item}" for idx, item in enumerate(questions, start=1))
    notify(
        job,
        deps.slack,
        [
            "I need more information before I can continue:",
            numbered,
            "",
            "Reply in this thread with `@devbot` and your answers.",
            "Or `@devbot just create the ticket` to stop questions and open GitHub now.",
            f"To drop this job: `@devbot remove {job.display_id}`",
        ],
    )
    if job.github_issue_number:
        _github_touch(
            job,
            deps,
            PipelinePhase(job.pipeline_phase or PipelinePhase.CREATE.value),
            f"**needs info** at {datetime.now(UTC).isoformat()}\n\n" + numbered,
            session,
        )
        session.commit()


def _maybe_cleanup(job: Job, deps: WorkerDeps) -> None:
    """Keep the ticket worktree after completion so `@devbot commit` can still flush it.

    Disk cleanup is `@devbot remove`. Auto-deleting here used to wipe uncommitted
    agent edits and made force-commit recreate an empty-looking tree.
    """
    del deps
    logger.info(
        "worktree_kept",
        extra=log_extra(job_id=job.id, repository=job.repository, stage=job.worktree_path),
    )


def _fail_or_retry(session: Session, job: Job, deps: WorkerDeps, exc: Exception) -> None:
    retryable = getattr(exc, "retryable", True)
    decision = next_retry(job.attempt, deps.settings.max_agent_attempts, retryable=retryable)
    message = safe_error(exc)
    logger.exception("job_failed", extra=log_extra(job_id=job.id, repository=job.repository, attempt=job.attempt, stage=job.current_stage))
    phase = PipelinePhase(job.pipeline_phase or PipelinePhase.CREATE.value)
    if decision.retry:
        job.attempt = decision.attempt
        job.last_error = message
        release_lease(session, job)
        if job.status != JobStatus.QUEUED.value:
            transition_job(session, job, JobStatus.QUEUED, event_type=JobEventType.RETRY_SCHEDULED)
        add_event(session, job, JobEventType.RETRY_SCHEDULED, payload={"attempt": job.attempt, "delay": decision.delay_seconds})
        session.commit()
        notify(job, deps.slack, [f"⚠ Attempt {job.attempt} failed: {message}", "→ Retrying"])
        return
    job.phase_results = mark_phase(job.phase_results, phase, PhaseStatus.FAILED, summary=message[:1000])
    if job.status not in {JobStatus.FAILED.value, JobStatus.CANCELLED.value, JobStatus.COMPLETED.value}:
        transition_job(session, job, JobStatus.FAILED, event_type=JobEventType.FAILED, stage=JobStage.FAILED, error=message)
    else:
        job.last_error = message
    release_lease(session, job)
    session.commit()
    issue = job.github_issue_number or ""
    notify(job, deps.slack, [f"✗ {job.issue_ref} `{phase.value}` failed", message, f"Reply `retry {issue}` to try again."])
    if job.github_issue_number:
        _github_touch(job, deps, phase, f"**{phase.value} failed** at {datetime.now(UTC).isoformat()}\n\n{message}", session)
        session.commit()
