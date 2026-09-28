from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.jobs.models import (
    PIPELINE_ORDER,
    CheckResult,
    MergeGateInputs,
    MergeGateResult,
    PhaseStatus,
    PipelinePhase,
    RetryDecision,
)


def empty_phase_results() -> dict[str, dict[str, Any]]:
    return {
        phase.value: {"status": PhaseStatus.PENDING.value, "skipped": False, "summary": "", "at": None}
        for phase in PIPELINE_ORDER
    }


def phase_entry(phase_results: dict[str, Any] | None, phase: PipelinePhase) -> dict[str, Any]:
    return dict((phase_results or {}).get(phase.value) or {})


def phase_is_done(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    if entry.get("skipped"):
        return True
    return entry.get("status") in {PhaseStatus.PASSED.value, PhaseStatus.SKIPPED.value}


def can_run_phase(phase_results: dict[str, Any] | None, requested: PipelinePhase) -> tuple[bool, str | None]:
    if requested == PipelinePhase.CREATE:
        return True, None
    idx = PIPELINE_ORDER.index(requested)
    for prior in PIPELINE_ORDER[:idx]:
        if not phase_is_done(phase_entry(phase_results, prior)):
            return False, f"Phase `{prior.value}` must pass or be skipped first."
    return True, None


def mark_phase(
    phase_results: dict[str, Any] | None,
    phase: PipelinePhase,
    status: PhaseStatus,
    *,
    summary: str = "",
) -> dict[str, dict[str, Any]]:
    results = empty_phase_results()
    results.update(phase_results or {})
    results[phase.value] = {
        "status": status.value,
        "skipped": status == PhaseStatus.SKIPPED,
        "summary": summary,
        "at": datetime.now(UTC).isoformat(),
    }
    return results


def next_phase(phase_results: dict[str, Any] | None) -> PipelinePhase | None:
    for phase in PIPELINE_ORDER:
        if not phase_is_done(phase_entry(phase_results, phase)):
            return phase
    return None


def calculate_review_gate(*results: object) -> tuple[bool, list[str]]:
    """Independent reviews must pass; blocking findings fail the gate."""
    blocking: list[str] = []
    for result in results:
        passed = bool(getattr(result, "passed", False))
        is_blocking = bool(getattr(result, "blocking", False))
        name = getattr(result, "summary", None) or type(result).__name__
        if is_blocking or not passed:
            blocking.append(str(name))
    return (not blocking, blocking)


def calculate_merge_gate(inputs: MergeGateInputs) -> MergeGateResult:
    checks = inputs.model_dump()
    blocking = [
        name
        for name, result in checks.items()
        if result in {CheckResult.FAIL, CheckResult.BLOCKED}
    ]
    return MergeGateResult(ready=not blocking, checks=checks, blocking=blocking)


def next_retry(attempt: int, max_attempts: int, *, retryable: bool, base_delay: float = 5.0) -> RetryDecision:
    nxt = attempt + 1
    if not retryable or nxt >= max_attempts:
        return RetryDecision(retry=False, attempt=nxt)
    delay = min(base_delay * (2 ** attempt), 60.0)
    return RetryDecision(retry=True, attempt=nxt, delay_seconds=delay)
