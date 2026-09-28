from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.security.secrets import redact_secrets

logger = logging.getLogger(__name__)


class SlackClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._token = settings.slack_bot_token
        self._client = client or httpx.Client(timeout=15.0)

    def post_message(self, channel: str, text: str, *, thread_ts: str | None = None) -> dict[str, Any]:
        if not self._token or not channel:
            logger.info("slack_skip_post", extra={"reason": "missing_token_or_channel"})
            return {}
        response = self._client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"channel": channel, "text": text, "thread_ts": thread_ts},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            logger.warning("slack_post_failed", extra={"error": redact_secrets(str(data.get("error")))})
        return data

    def list_thread_replies(self, channel: str, thread_ts: str) -> list[dict[str, Any]]:
        token = getattr(self, "_token", "")
        if not token or not channel or not thread_ts:
            return []
        messages: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(20):
            params: dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            response = self._client.get(
                "https://slack.com/api/conversations.replies",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("slack_thread_failed", extra={"error": redact_secrets(str(data.get("error")))})
                break
            messages.extend(data.get("messages") or [])
            cursor = str((data.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                break
        return messages
