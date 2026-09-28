from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import IncomingWebhookEvent, Job, JobEvent
from app.jobs.gates import empty_phase_results
from app.jobs.models import (
    JobEventType,
    JobStage,
    JobStatus,
    PipelinePhase,
    branch_name_for_issue,
)
from app.jobs.state_machine import validate_transition
from app.security.secrets import redact_secrets


def utcnow() -> datetime:
    return datetime.now(UTC)


def add_event(
    session: Session,
    job: Job,
    event_type: JobEventType | str,
    *,
    payload: dict[str, Any] | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
) -> JobEvent:
    event = JobEvent(
        job_id=job.id,
        event_type=str(event_type),
        from_status=from_status,
        to_status=to_status,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


def create_job(
    session: Session,
    *,
    external_key: str,
    repository: str,
    request: str,
    slack_channel: str | None = None,
    slack_thread_ts: str | None = None,
    requested_by: str | None = None,
    credential_user: str | None = None,
    pipeline_phase: PipelinePhase = PipelinePhase.CREATE,
    github_issue_number: int | None = None,
    github_issue_url: str | None = None,
    phase_results: dict[str, Any] | None = None,
    specification: dict[str, Any] | None = None,
) -> tuple[Job, bool]:
    existing = session.scalar(select(Job).where(Job.external_key == external_key))
    if existing:
        return existing, False
    results = phase_results or empty_phase_results()
    job = Job(
        external_key=external_key,
        repository=repository,
        request=request,
        slack_channel=slack_channel,
        slack_thread_ts=slack_thread_ts,
        requested_by=requested_by,
        credential_user=credential_user,
        status=JobStatus.QUEUED.value,
        current_stage=JobStage.QUEUED.value,
        pipeline_phase=pipeline_phase.value,
        phase_results=results,
        github_issue_number=github_issue_number,
        github_issue_url=github_issue_url,
        specification=specification,
        attempt=0,
    )
    session.add(job)
    session.flush()
    if github_issue_number:
        job.branch_name = branch_name_for_issue(github_issue_number)
    add_event(session, job, JobEventType.JOB_CREATED, to_status=job.status, payload={"repository": repository})
    return job, True


def transition_job(
    session: Session,
    job: Job,
    target: JobStatus,
    *,
    event_type: JobEventType | None = None,
    stage: JobStage | str | None = None,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> Job:
    current = JobStatus(job.status)
    validate_transition(current, target)
    job.status = target.value
    job.updated_at = utcnow()
    if stage is not None:
        job.current_stage = str(stage)
    if error is not None:
        job.last_error = redact_secrets(error)
    if target in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        job.completed_at = utcnow()
        job.lease_expires_at = None
        job.claimed_by = None
    elif target in {JobStatus.QUEUED, JobStatus.IDLE, JobStatus.AWAITING_INPUT}:
        job.completed_at = None
    add_event(
        session,
        job,
        event_type or _default_event(target),
        from_status=current.value,
        to_status=target.value,
        payload=payload,
    )
    session.flush()
    return job


def _default_event(target: JobStatus) -> JobEventType:
    mapping = {
        JobStatus.COMPLETED: JobEventType.COMPLETED,
        JobStatus.FAILED: JobEventType.FAILED,
        JobStatus.CANCELLED: JobEventType.CANCELLED,
        JobStatus.RUNNING: JobEventType.PHASE_STARTED,
        JobStatus.IDLE: JobEventType.PHASE_COMPLETED,
        JobStatus.AWAITING_INPUT: JobEventType.NEED_INFO,
        JobStatus.QUEUED: JobEventType.RETRY_SCHEDULED,
    }
    return mapping.get(target, JobEventType.JOB_CLAIMED)


def get_job(session: Session, job_id: int) -> Job | None:
    return session.get(Job, job_id)


def find_jobs_by_issue(session: Session, issue_number: int, repository: str | None = None) -> list[Job]:
    stmt = select(Job).where(Job.github_issue_number == issue_number).order_by(Job.id)
    if repository:
        stmt = stmt.where(Job.repository == repository)
    return list(session.scalars(stmt))


def find_job_by_thread(session: Session, channel: str, thread_ts: str) -> Job | None:
    return session.scalar(
        select(Job)
        .where(Job.slack_channel == channel, Job.slack_thread_ts == thread_ts)
        .order_by(Job.id.desc())
    )


def list_pipeline_jobs(session: Session, *, limit: int = 30) -> list[Job]:
    return list(
        session.scalars(
            select(Job)
            .where(Job.status != JobStatus.CANCELLED.value)
            .order_by(Job.updated_at.desc())
            .limit(limit)
        )
    )


def attach_github_issue(job: Job, number: int, url: str | None) -> None:
    job.github_issue_number = number
    job.github_issue_url = url
    job.branch_name = branch_name_for_issue(number)


def claim_next_job(session: Session, worker_id: str, settings: Settings) -> Job | None:
    now = utcnow()
    runnable = [JobStatus.QUEUED.value, JobStatus.RUNNING.value]
    stmt = (
        select(Job)
        .where(
            Job.status.in_(runnable),
            or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
        )
        .order_by(Job.id)
        .limit(8)
    )
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    candidates = list(session.scalars(stmt))
    lease = now + timedelta(seconds=settings.job_lease_seconds)
    for job in candidates:
        result = session.execute(
            update(Job)
            .where(
                Job.id == job.id,
                or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
            )
            .values(claimed_by=worker_id, lease_expires_at=lease, updated_at=now)
        )
        if result.rowcount == 1:
            session.flush()
            session.refresh(job)
            add_event(session, job, JobEventType.JOB_CLAIMED, payload={"worker_id": worker_id, "attempt": job.attempt})
            return job
    return None


def renew_lease(session: Session, job: Job, worker_id: str, settings: Settings) -> None:
    if job.claimed_by != worker_id:
        return
    job.lease_expires_at = utcnow() + timedelta(seconds=settings.job_lease_seconds)
    job.updated_at = utcnow()
    session.flush()


def release_lease(session: Session, job: Job) -> None:
    job.claimed_by = None
    job.lease_expires_at = None
    job.updated_at = utcnow()
    session.flush()


def record_incoming_event(
    session: Session,
    *,
    source: str,
    delivery_id: str,
    event_type: str,
    payload: dict[str, Any] | None,
) -> tuple[IncomingWebhookEvent, bool]:
    existing = session.scalar(
        select(IncomingWebhookEvent).where(
            IncomingWebhookEvent.source == source,
            IncomingWebhookEvent.delivery_id == delivery_id,
        )
    )
    if existing:
        return existing, False
    row = IncomingWebhookEvent(
        source=source,
        delivery_id=delivery_id,
        event_type=event_type,
        payload=payload,
        processed=True,
    )
    session.add(row)
    session.flush()
    return row, True
