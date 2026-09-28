from __future__ import annotations

from app.github.webhooks import handle_github_event


def test_github_event_persisted_and_duplicate_ignored(session) -> None:
    payload = {"action": "opened", "repository": {"full_name": "acme/myapp"}, "pull_request": {"number": 9}}
    first = handle_github_event(session, event_type="pull_request", delivery_id="deliv-1", payload=payload)
    second = handle_github_event(session, event_type="pull_request", delivery_id="deliv-1", payload=payload)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["id"] == second["id"]


def test_unknown_github_event_ignored(session) -> None:
    result = handle_github_event(session, event_type="star", delivery_id="deliv-2", payload={})
    assert result["ignored"] is True
