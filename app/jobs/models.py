from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    AWAITING_INPUT = "AWAITING_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PipelinePhase(StrEnum):
    CREATE = "create"
    IMPLEMENT = "implement"
    TEST = "test"
    SECURITY = "security"
    ARCHITECT = "architect"
    DOCUMENT = "document"


class PhaseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_INPUT = "awaiting_input"


PIPELINE_ORDER: tuple[PipelinePhase, ...] = (
    PipelinePhase.CREATE,
    PipelinePhase.IMPLEMENT,
    PipelinePhase.TEST,
    PipelinePhase.SECURITY,
    PipelinePhase.ARCHITECT,
    PipelinePhase.DOCUMENT,
)


class JobStage(StrEnum):
    QUEUED = "queued"
    CREATE = "create"
    IMPLEMENT = "implement"
    TEST = "test"
    SECURITY = "security"
    ARCHITECT = "architect"
    DOCUMENT = "document"
    WORKTREE = "worktree"
    CODING = "coding"
    TESTING = "testing"
    COMMITTING = "committing"
    PUSHING = "pushing"
    CREATING_PR = "creating_pr"
    PLANNING = "planning"
    COMPLETED = "completed"
    FAILED = "failed"


class JobEventType(StrEnum):
    SLACK_REQUESTED = "SLACK_REQUESTED"
    JOB_CREATED = "JOB_CREATED"
    JOB_CLAIMED = "JOB_CLAIMED"
    JOB_LEASE_RENEWED = "JOB_LEASE_RENEWED"
    REFINEMENT_STARTED = "REFINEMENT_STARTED"
    REFINEMENT_COMPLETED = "REFINEMENT_COMPLETED"
    NEED_INFO = "NEED_INFO"
    ISSUE_CREATED = "ISSUE_CREATED"
    ISSUE_UPDATED = "ISSUE_UPDATED"
    PHASE_STARTED = "PHASE_STARTED"
    PHASE_COMPLETED = "PHASE_COMPLETED"
    PHASE_SKIPPED = "PHASE_SKIPPED"
    WORKTREE_CREATED = "WORKTREE_CREATED"
    PLAN_STARTED = "PLAN_STARTED"
    PLAN_COMPLETED = "PLAN_COMPLETED"
    CODING_STARTED = "CODING_STARTED"
    CODING_COMPLETED = "CODING_COMPLETED"
    TESTS_STARTED = "TESTS_STARTED"
    TESTS_PASSED = "TESTS_PASSED"
    TESTS_FAILED = "TESTS_FAILED"
    TESTS_NOT_APPLICABLE = "TESTS_NOT_APPLICABLE"
    COMMIT_CREATED = "COMMIT_CREATED"
    BRANCH_PUSHED = "BRANCH_PUSHED"
    PR_CREATED = "PR_CREATED"
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_FAILED = "REVIEW_FAILED"
    FIX_REQUESTED = "FIX_REQUESTED"
    SECURITY_STARTED = "SECURITY_STARTED"
    SECURITY_FAILED = "SECURITY_FAILED"
    DOCUMENTATION_STARTED = "DOCUMENTATION_STARTED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PROJECT_SYNC_FAILED = "PROJECT_SYNC_FAILED"


class SlackCommand(StrEnum):
    CREATE = "create"
    IMPLEMENT = "implement"
    TEST = "test"
    SECURITY = "security"
    ARCHITECT = "architect"
    DOCUMENT = "document"
    COMMIT = "commit"
    AUTOPILOT = "autopilot"
    HELP = "help"
    STATUS = "status"
    CANCEL = "cancel"
    REMOVE = "remove"
    RETRY = "retry"
    PING = "ping"


PIPELINE_COMMANDS = frozenset(
    {
        SlackCommand.CREATE,
        SlackCommand.IMPLEMENT,
        SlackCommand.TEST,
        SlackCommand.SECURITY,
        SlackCommand.ARCHITECT,
        SlackCommand.DOCUMENT,
    }
)

PHASE_COMMANDS = frozenset(PIPELINE_COMMANDS - {SlackCommand.CREATE})


class CheckResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"


class Finding(BaseModel):
    severity: str
    category: str
    description: str
    evidence: str = ""
    recommended_fix: str = ""
    blocking: bool = False


class AgentReviewResult(BaseModel):
    passed: bool
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    tests_run: list[str] = Field(default_factory=list)
    blocking: bool = False
    status: CheckResult = CheckResult.PASS


class MergeGateInputs(BaseModel):
    acceptance_criteria: CheckResult
    tests: CheckResult
    regression_tests: CheckResult
    architecture_review: CheckResult
    security_review: CheckResult
    github_ci: CheckResult
    no_merge_conflicts: CheckResult


class MergeGateResult(BaseModel):
    ready: bool
    checks: dict[str, CheckResult]
    blocking: list[str] = Field(default_factory=list)


class RetryDecision(BaseModel):
    retry: bool
    attempt: int
    delay_seconds: float = 0


_DEV_ID_RE = re.compile(r"^DEV-(\d+)$", re.IGNORECASE)


def job_display_id(job_id: int) -> str:
    return f"DEV-{job_id}"


def parse_job_display_id(value: str) -> int | None:
    text = value.strip()
    if text.upper().startswith("DEV-"):
        text = text[4:]
    if text.isdigit():
        return int(text)
    return None


def parse_github_issue_number(value: str) -> int | None:
    text = (value or "").strip()
    if _DEV_ID_RE.match(text):
        return None
    if text.startswith("#"):
        text = text[1:]
    if text.isdigit():
        return int(text)
    return None


def command_to_phase(command: SlackCommand) -> PipelinePhase | None:
    try:
        return PipelinePhase(command.value)
    except ValueError:
        return None


def branch_name_for(job_id: int) -> str:
    return f"agent/{job_display_id(job_id)}"


def branch_name_for_issue(issue_number: int) -> str:
    return f"agent/issue-{issue_number}"
