from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.jobs.service import record_incoming_event

HANDLED_EVENTS = {"pull_request", "pull_request_review", "workflow_run", "check_run", "push"}


def handle_github_event(
    session: Session,
    *,
    event_type: str,
    delivery_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    row, created = record_incoming_event(
        session,
        source="github",
        delivery_id=delivery_id or "missing",
        event_type=event_type,
        payload=_summary(event_type, payload),
    )
    if not created:
        return {"ok": True, "duplicate": True, "id": row.id}
    if event_type not in HANDLED_EVENTS:
        return {"ok": True, "ignored": True, "id": row.id}
    return {"ok": True, "duplicate": False, "id": row.id, "event": event_type}


def _summary(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo = (payload.get("repository") or {}).get("full_name")
    action = payload.get("action")
    number = (payload.get("pull_request") or payload.get("check_run") or {}).get("number") or (
        payload.get("check_run") or {}
    ).get("id")
    return {"event": event_type, "action": action, "repository": repo, "number": number}
