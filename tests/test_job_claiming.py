from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import Job
from app.jobs.models import JobStatus
from app.jobs.service import claim_next_job, create_job


def test_claim_is_exclusive(session, settings) -> None:
    create_job(session, external_key="a", repository="acme/myapp", request="one")
    create_job(session, external_key="b", repository="acme/myapp", request="two")
    session.commit()
    first = claim_next_job(session, "w1", settings)
    second = claim_next_job(session, "w2", settings)
    assert first is not None and second is not None
    assert first.id != second.id
    third = claim_next_job(session, "w3", settings)
    assert third is None


def test_expired_lease_is_reclaimed(session, settings) -> None:
    job, _ = create_job(session, external_key="c", repository="acme/myapp", request="retry")
    job.status = JobStatus.RUNNING.value
    job.claimed_by = "dead-worker"
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
    session.commit()
    claimed = claim_next_job(session, "alive", settings)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.claimed_by == "alive"


def test_duplicate_external_key_does_not_create_second_job(session) -> None:
    first, created1 = create_job(session, external_key="same", repository="acme/myapp", request="x")
    second, created2 = create_job(session, external_key="same", repository="acme/myapp", request="x")
    assert created1 is True
    assert created2 is False
    assert first.id == second.id
    assert session.query(Job).count() == 1
