from __future__ import annotations

from app.jobs.gates import next_retry


def test_retry_backoff_and_cap() -> None:
    first = next_retry(0, 3, retryable=True, base_delay=5)
    assert first.retry is True
    assert first.attempt == 1
    assert first.delay_seconds == 5
    second = next_retry(1, 3, retryable=True, base_delay=5)
    assert second.delay_seconds == 10
    exhausted = next_retry(2, 3, retryable=True)
    assert exhausted.retry is False
    not_retryable = next_retry(0, 3, retryable=False)
    assert not_retryable.retry is False
