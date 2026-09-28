from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": True,
        "slack_token_configured": bool(settings.slack_bot_token),
        "slack_signing_configured": bool(settings.slack_signing_secret),
        "github_token_configured": bool(settings.github_token),
        "cursor_key_configured": bool(settings.cursor_api_key),
    }
