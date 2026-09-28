from __future__ import annotations

from app.jobs.models import JobStatus

TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.IDLE, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.IDLE,
            JobStatus.AWAITING_INPUT,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.COMPLETED,
            JobStatus.QUEUED,
        }
    ),
    JobStatus.IDLE: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.COMPLETED}),
    JobStatus.AWAITING_INPUT: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.IDLE, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset({JobStatus.QUEUED}),
    JobStatus.CANCELLED: frozenset({JobStatus.QUEUED}),
}


class InvalidTransition(Exception):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition {current} -> {target}")


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    if current == target:
        return
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
