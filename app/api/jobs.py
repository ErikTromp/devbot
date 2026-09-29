from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Job, JobEvent
from app.db.session import get_db
from app.jobs.board import activity_label, board_phase, display_phase_results, event_label, next_action_lines, next_user_phase, read_job_plan, specification_subset, status_counts
from app.jobs.models import parse_job_display_id
from app.jobs.service import create_job, list_pipeline_jobs
from app.repos import board_repo_options
from app.slack.parser import normalize_repo

router = APIRouter()


class CreateJobRequest(BaseModel):
    repository: str
    request: str
    external_key: str | None = None


def _resolve_job(session: Session, job_id: str) -> Job:
    numeric = parse_job_display_id(job_id) if not job_id.isdigit() else int(job_id)
    if numeric is None:
        raise HTTPException(status_code=404, detail="Invalid job id")
    job = session.get(Job, numeric)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _job_payload(job: Job) -> dict[str, Any]:
    nxt = next_user_phase(job)
    return {
        "id": job.id,
        "display_id": job.display_id,
        "external_key": job.external_key,
        "repository": job.repository,
        "repo_alias": get_settings().repo_catalog().alias_for(job.repository),
        "request": job.request,
        "slack_channel": job.slack_channel,
        "slack_thread_ts": job.slack_thread_ts,
        "requested_by": job.requested_by,
        "credential_user": job.credential_user,
        "status": job.status,
        "current_stage": job.current_stage,
        "pipeline_phase": job.pipeline_phase,
        "phase_results": display_phase_results(job),
        "github_issue_number": job.github_issue_number,
        "github_issue_url": job.github_issue_url,
        "issue_ref": job.issue_ref,
        "issue_title": job.issue_title,
        "branch_name": job.branch_name,
        "worktree_path": job.worktree_path,
        "pull_request_number": job.pull_request_number,
        "pull_request_url": job.pull_request_url,
        "attempt": job.attempt,
        "last_error": job.last_error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
        "next_action": activity_label(job) or next_action_lines(job)[0],
        "activity": activity_label(job),
        "next_phase": nxt.value if nxt else None,
        "board_phase": board_phase(job),
        "specification": specification_subset(job),
    }


def _event_payload(event: JobEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "label": event_label(event.event_type),
        "from_status": event.from_status,
        "to_status": event.to_status,
        "payload": event.payload,
        "created_at": event.created_at,
    }


def list_job_events(session: Session, job: Job) -> list[JobEvent]:
    return list(session.scalars(select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id)))


@router.post("/jobs")
def create_job_endpoint(
    body: CreateJobRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    repo = settings.repo_catalog().resolve(body.repository, settings.default_github_org) or normalize_repo(
        body.repository, settings.default_github_org
    )
    if not repo:
        raise HTTPException(status_code=400, detail="Invalid repository")
    key = body.external_key or f"api:{repo}:{body.request[:80]}"
    job, created = create_job(session, external_key=key, repository=repo, request=body.request)
    return {"created": created, "job": _job_payload(job)}


@router.get("/jobs")
def list_jobs_endpoint(session: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    jobs = list_pipeline_jobs(session)
    catalog = settings.repo_catalog()
    return {
        "jobs": [_job_payload(job) for job in jobs],
        "counts": status_counts(jobs),
        "repos": board_repo_options(catalog, [job.repository for job in jobs]),
    }


@router.get("/jobs/{job_id}")
def get_job_endpoint(job_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    return _job_payload(_resolve_job(session, job_id))


@router.get("/jobs/{job_id}/plan")
def get_job_plan(job_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    job = _resolve_job(session, job_id)
    plan = read_job_plan(job)
    return {
        "job_id": job.id,
        "path": plan["path"],
        "markdown": plan["markdown"],
        "summary": plan["summary"],
        "source": plan["source"],
    }


@router.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    job = _resolve_job(session, job_id)
    return {
        "job_id": job.id,
        "events": [_event_payload(event) for event in list_job_events(session, job)],
    }
