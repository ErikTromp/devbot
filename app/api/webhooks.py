from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.github.signatures import GitHubSignatureError, verify_github_signature
from app.github.webhooks import handle_github_event
from app.jobs.service import record_incoming_event
from app.slack.client import SlackClient
from app.slack.events import SlackCommandError, handle_slack_event
from app.slack.signatures import SlackSignatureError, verify_slack_signature

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/slack/events")
async def slack_events(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    body = await request.body()
    try:
        verify_slack_signature(
            secret=settings.slack_signing_secret,
            timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
            body=body,
            provided=request.headers.get("X-Slack-Signature", ""),
            max_age_seconds=settings.slack_max_age_seconds,
        )
    except SlackSignatureError as exc:
        logger.warning("slack_signature_rejected", extra={"error": str(exc)})
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    payload = json.loads(body or b"{}")
    logger.info(
        "slack_http",
        extra={"slack_type": payload.get("type"), "event": (payload.get("event") or {}).get("type")},
    )
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge") or ""})
    delivery = request.headers.get("X-Slack-Retry-Num") or payload.get("event_id") or "slack-unknown"
    _, created = record_incoming_event(
        session,
        source="slack",
        delivery_id=str(payload.get("event_id") or delivery),
        event_type=payload.get("type") or "unknown",
        payload={"type": payload.get("type"), "event": (payload.get("event") or {}).get("type")},
    )
    if not created:
        return JSONResponse({"ok": True, "duplicate": True})
    slack = SlackClient(settings)
    try:
        result = handle_slack_event(session, payload, settings, slack)
    except SlackCommandError as exc:
        logger.info("slack_command_error", extra={"error": str(exc)})
        return JSONResponse({"ok": True, "error": str(exc)})
    return JSONResponse(result)


@router.post("/github/events")
async def github_events(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    body = await request.body()
    try:
        verify_github_signature(
            secret=settings.github_webhook_secret,
            body=body,
            provided=request.headers.get("X-Hub-Signature-256", ""),
        )
    except GitHubSignatureError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    payload = json.loads(body or b"{}")
    result = handle_github_event(
        session,
        event_type=request.headers.get("X-GitHub-Event", "unknown"),
        delivery_id=request.headers.get("X-GitHub-Delivery", ""),
        payload=payload,
    )
    return JSONResponse(result)
