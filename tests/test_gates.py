from __future__ import annotations

from types import SimpleNamespace

from app.jobs.gates import calculate_merge_gate, calculate_review_gate, can_run_phase, empty_phase_results, mark_phase
from app.jobs.models import CheckResult, MergeGateInputs, PhaseStatus, PipelinePhase


def test_review_gate_blocks_on_failure() -> None:
    ok, blocking = calculate_review_gate(
        SimpleNamespace(passed=True, blocking=False, summary="tests"),
        SimpleNamespace(passed=False, blocking=True, summary="security"),
    )
    assert ok is False
    assert "security" in blocking


def test_merge_gate_requires_all_non_failing() -> None:
    inputs = MergeGateInputs(
        acceptance_criteria=CheckResult.PASS,
        tests=CheckResult.PASS,
        regression_tests=CheckResult.NOT_APPLICABLE,
        architecture_review=CheckResult.PASS,
        security_review=CheckResult.FAIL,
        github_ci=CheckResult.PASS,
        no_merge_conflicts=CheckResult.PASS,
    )
    result = calculate_merge_gate(inputs)
    assert result.ready is False
    assert "security_review" in result.blocking

    inputs.security_review = CheckResult.PASS
    assert calculate_merge_gate(inputs).ready is True


def test_phase_order_requires_prior_pass_or_skip() -> None:
    results = empty_phase_results()
    ok, reason = can_run_phase(results, PipelinePhase.TEST)
    assert ok is False
    assert reason and "create" in reason
    assert can_run_phase(results, PipelinePhase.CREATE)[0] is True

    results = mark_phase(results, PipelinePhase.CREATE, PhaseStatus.PASSED)
    assert can_run_phase(results, PipelinePhase.IMPLEMENT)[0] is True
    assert can_run_phase(results, PipelinePhase.SECURITY)[0] is False
    assert can_run_phase(results, PipelinePhase.TEST)[0] is False

    results = mark_phase(results, PipelinePhase.IMPLEMENT, PhaseStatus.SKIPPED)
    assert can_run_phase(results, PipelinePhase.SECURITY)[0] is True
    assert can_run_phase(results, PipelinePhase.TEST)[0] is False

    results = mark_phase(results, PipelinePhase.SECURITY, PhaseStatus.PASSED)
    results = mark_phase(results, PipelinePhase.ARCHITECT, PhaseStatus.PASSED)
    assert can_run_phase(results, PipelinePhase.TEST)[0] is True
    assert can_run_phase(results, PipelinePhase.DOCUMENT)[0] is False
