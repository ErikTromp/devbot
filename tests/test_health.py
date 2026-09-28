from __future__ import annotations


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["coding_agent"] == "cursor"
    assert payload["copilot_key_configured"] is False
