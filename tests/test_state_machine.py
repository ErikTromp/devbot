from __future__ import annotations

import pytest

from app.jobs.models import JobStatus
from app.jobs.state_machine import InvalidTransition, can_transition, validate_transition


def test_legal_pipeline_path() -> None:
    assert can_transition(JobStatus.QUEUED, JobStatus.RUNNING)
    assert can_transition(JobStatus.RUNNING, JobStatus.IDLE)
    assert can_transition(JobStatus.IDLE, JobStatus.QUEUED)
    assert can_transition(JobStatus.RUNNING, JobStatus.AWAITING_INPUT)
    assert can_transition(JobStatus.AWAITING_INPUT, JobStatus.QUEUED)
    assert can_transition(JobStatus.RUNNING, JobStatus.COMPLETED)
    assert can_transition(JobStatus.FAILED, JobStatus.QUEUED)
    assert can_transition(JobStatus.COMPLETED, JobStatus.QUEUED)


def test_invalid_transition_rejected() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(JobStatus.QUEUED, JobStatus.COMPLETED)
    with pytest.raises(InvalidTransition):
        validate_transition(JobStatus.CANCELLED, JobStatus.IDLE)
