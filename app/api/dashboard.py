from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.auth import SESSION_USER_KEY, credentials_match, is_signed_in, safe_next_path
from app.api.jobs import _event_payload, _job_payload, _resolve_job, list_job_events
from app.config import Settings, get_settings
from app.db.session import get_db
from app.jobs.board import read_job_plan, render_plan_markdown, status_counts
from app.jobs.models import PIPELINE_ORDER, JobStage
from app.jobs.service import list_pipeline_jobs

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_IMPLEMENT_STAGES = (
    JobStage.WORKTREE.value,
    JobStage.PLANNING.value,
    JobStage.CODING.value,
    JobStage.TESTING.value,
    JobStage.COMMITTING.value,
    JobStage.PUSHING.value,
    JobStage.CREATING_PR.value,
)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M UTC")


templates.env.filters["when"] = _fmt_dt
templates.env.filters["markdown"] = render_plan_markdown


def _phase_rows(job_payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = job_payload.get("phase_results") or {}
    current = job_payload.get("pipeline_phase")
    rows = []
    for phase in PIPELINE_ORDER:
        entry = results.get(phase.value) or {}
        status = str(entry.get("status") or "pending")
        rows.append(
            {
                "name": phase.value,
                "status": status,
                "summary": entry.get("summary") or "",
                "at": entry.get("at"),
                "current": current == phase.value,
            }
        )
    return rows


def _ticket_context(request: Request, session, job) -> dict[str, Any]:
    payload = _job_payload(job)
    plan = read_job_plan(job)
    stage = payload.get("current_stage")
    return {
        **_auth_context(request),
        "job": payload,
        "phases": _phase_rows(payload),
        "plan": plan,
        "events": [_event_payload(event) for event in list_job_events(session, job)],
        "implement_stages": _IMPLEMENT_STAGES,
        "show_implement_stages": payload.get("pipeline_phase") == "implement"
        and (payload.get("status") == "RUNNING" or stage in _IMPLEMENT_STAGES),
    }


def _auth_context(request: Request, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {"signed_in": is_signed_in(request, settings.dashboard_username)}


@router.get("/", response_class=HTMLResponse)
def pipeline_board(request: Request, session=Depends(get_db), repo: str = "") -> HTMLResponse:
    jobs = list_pipeline_jobs(session)
    repos = sorted({job.repository for job in jobs if job.repository})
    selected = repo.strip()
    visible = [job for job in jobs if not selected or job.repository == selected]
    payloads = [_job_payload(job) for job in visible]
    columns = {phase.value: [] for phase in PIPELINE_ORDER}
    for payload in payloads:
        columns.setdefault(payload["board_phase"], []).append(payload)
    return templates.TemplateResponse(
        request,
        "board.html",
        {
            **_auth_context(request),
            "jobs": payloads,
            "columns": columns,
            "phases": [phase.value for phase in PIPELINE_ORDER],
            "counts": status_counts(visible),
            "repos": repos,
            "selected_repo": selected,
        },
    )


@router.get("/tickets/{job_id}", response_class=HTMLResponse)
def ticket_detail(job_id: str, request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    try:
        job = _resolve_job(session, job_id)
    except HTTPException:
        return templates.TemplateResponse(
            request,
            "not_found.html",
            {**_auth_context(request), "job_id": job_id},
            status_code=404,
        )
    return templates.TemplateResponse(request, "ticket.html", _ticket_context(request, session, job))


@router.get("/login", response_model=None)
def login_form(request: Request, next: str = "/", settings: Settings = Depends(get_settings)):
    if is_signed_in(request, settings.dashboard_username):
        return RedirectResponse(safe_next_path(next), status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": safe_next_path(next), "error": None},
    )


@router.post("/login", response_model=None)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    settings: Settings = Depends(get_settings),
):
    nxt = safe_next_path(next)
    if credentials_match(username, password, settings.dashboard_username, settings.dashboard_password):
        request.session[SESSION_USER_KEY] = settings.dashboard_username
        return RedirectResponse(nxt, status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": nxt, "error": "That username or password is wrong."},
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
