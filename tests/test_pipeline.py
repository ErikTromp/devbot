from __future__ import annotations

from pathlib import Path

from app.agents.base import AgentContext, AgentResult
from app.jobs.models import CheckResult, JobStatus, PipelinePhase
from app.jobs.pipeline import WorkerDeps, run_claimed_job
from app.jobs.service import create_job
from app.slack.client import SlackClient


class FakeSlack(SlackClient):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def post_message(self, channel: str, text: str, *, thread_ts: str | None = None) -> dict:
        self.messages.append(text)
        return {"ok": True}

    def list_thread_replies(self, channel: str, thread_ts: str) -> list:
        return []


class FakeCoding:
    name = "coding"

    def plan(self, context: AgentContext) -> AgentResult:
        return AgentResult(ok=True, summary="plan", structured={"decision": "continue", "plan": "Touch feature.txt"})

    def run(self, context: AgentContext) -> AgentResult:
        (context.worktree / "feature.txt").write_text("export", encoding="utf-8")
        return AgentResult(ok=True, summary="edited files", structured={"decision": "continue", "acceptance_criteria_met": True})


class FakeTests:
    name = "testing"

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(ok=True, summary="passed", structured={"status": CheckResult.PASS.value, "tests_run": ["pytest"]})


class FakeGitHub:
    def create_pull_request(self, repository, **kwargs):
        return {"number": 184, "html_url": f"https://github.com/{repository}/pull/184"}

    def update_pull_request(self, repository, number, **kwargs):
        return {}

    def create_issue(self, repository, **kwargs):
        return {"number": 9, "html_url": f"https://github.com/{repository}/issues/9"}

    def get_issue(self, repository, number):
        return {"number": number, "title": "Landing", "body": "## Acceptance Criteria\n- [ ] Hero", "html_url": f"https://github.com/{repository}/issues/{number}"}

    def comment_on_issue(self, repository, number, body):
        return {}

    def list_issue_comments(self, repository, number):
        return []

    def set_pipeline_label(self, repository, number, phase):
        return {}

    def assign_issue(self, repository, number, username=None):
        return {}

    def sync_pipeline_project(self, **kwargs):
        return {}


def _fake_push(self, branch: str):
    from app.git.repository import GitResult

    return GitResult(args=["git", "push", branch], returncode=0, stdout="", stderr="")


def _init_worktree(path: Path) -> None:
    import subprocess

    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)


def _implement_job(session, **kwargs):
    job, _ = create_job(
        session,
        repository="acme/myapp",
        pipeline_phase=PipelinePhase.IMPLEMENT,
        github_issue_number=9,
        github_issue_url="https://github.com/acme/myapp/issues/9",
        **kwargs,
    )
    return job


def test_pipeline_creates_pr_when_diff_and_tests_pass(session, settings, tmp_path, monkeypatch) -> None:
    job = _implement_job(
        session,
        external_key="p1",
        request="CSV export",
        slack_channel="C1",
        slack_thread_ts="1.2",
    )
    session.commit()
    worktree = tmp_path / "wt"
    _init_worktree(worktree)
    job.worktree_path = str(worktree)
    job.current_stage = "worktree"
    session.commit()

    monkeypatch.setattr("app.jobs.pipeline.ensure_repo_cache", lambda *a, **k: None)

    def fake_push(self, branch: str):
        from app.git.repository import GitResult

        return GitResult(args=["git", "push"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.git.repository.GitRepository.push", fake_push)

    settings.base_url = "https://devbot.example"
    slack = FakeSlack()
    deps = WorkerDeps(
        settings=settings,
        slack=slack,
        github=FakeGitHub(),
        coding=FakeCoding(),
        testing=FakeTests(),
    )
    job.status = JobStatus.QUEUED.value
    session.commit()
    run_claimed_job(session, job, deps, "w1")
    session.refresh(job)
    assert job.status == JobStatus.IDLE.value
    assert job.pull_request_number == 184
    assert "184" in (job.pull_request_url or "")
    assert job.phase_results["implement"]["status"] == "passed"
    assert (job.specification or {}).get("implementation_plan_path") == ".devbot/plan.md"
    assert "Touch feature.txt" in (job.specification or {}).get("implementation_plan_summary", "")
    assert (worktree / ".devbot" / "plan.md").read_text(encoding="utf-8").startswith("Touch feature.txt")
    done = "\n".join(slack.messages)
    assert any("Plan ready" in msg and ".devbot/plan.md" in msg for msg in slack.messages)
    assert f"https://devbot.example/tickets/{job.display_id}" in done
    assert "Touch feature.txt" not in done
    assert job.worktree_path
    assert "test skip 9" in done
    assert "@devbot test 9" in done


def test_pipeline_rejects_status_text_as_plan(session, settings, tmp_path, monkeypatch) -> None:
    job = _implement_job(session, external_key="p-narration", request="landscape")
    worktree = tmp_path / "wt-narration"
    _init_worktree(worktree)
    job.worktree_path = str(worktree)
    job.status = JobStatus.RUNNING.value
    job.current_stage = "worktree"
    session.commit()
    monkeypatch.setattr("app.jobs.pipeline.ensure_repo_cache", lambda *a, **k: None)
    monkeypatch.setattr("app.git.repository.GitRepository.push", _fake_push)
    narration = (
        "Researching the codebase and .devbot/plan.md to draft a concrete implementation plan. "
        "I have enough context to draft the implementation plan. .devbot/plan.md is missing in this worktree; "
        "the plan follows the GitHub issue and repo audit."
    )

    class NarrationCoding:
        def plan(self, context: AgentContext) -> AgentResult:
            return AgentResult(ok=True, summary=narration, structured={"result": narration})

        def run(self, context: AgentContext) -> AgentResult:
            return AgentResult(ok=False, summary="should not implement")

    settings.max_agent_attempts = 1
    deps = WorkerDeps(
        settings=settings,
        slack=FakeSlack(),
        github=FakeGitHub(),
        coding=NarrationCoding(),
        testing=FakeTests(),
    )
    run_claimed_job(session, job, deps, "w1")
    session.refresh(job)
    assert job.status == JobStatus.FAILED.value
    assert job.last_error and "status update" in job.last_error
    assert not (worktree / ".devbot" / "plan.md").exists()


def test_pipeline_fails_without_diff(session, settings, tmp_path, monkeypatch) -> None:
    job = _implement_job(session, external_key="p2", request="noop")
    worktree = tmp_path / "wt2"
    _init_worktree(worktree)
    job.worktree_path = str(worktree)
    job.status = JobStatus.RUNNING.value
    job.current_stage = "worktree"
    session.commit()
    monkeypatch.setattr("app.jobs.pipeline.ensure_repo_cache", lambda *a, **k: None)
    monkeypatch.setattr("app.git.repository.GitRepository.push", _fake_push)

    class EmptyCoding:
        def run(self, context: AgentContext) -> AgentResult:
            return AgentResult(ok=True, summary="I totally did it", structured={"decision": "continue"})

    deps = WorkerDeps(settings=settings, slack=FakeSlack(), github=FakeGitHub(), coding=EmptyCoding(), testing=FakeTests())
    settings.max_agent_attempts = 1
    run_claimed_job(session, job, deps, "w1")
    session.refresh(job)
    assert job.status == JobStatus.FAILED.value
    assert job.last_error and "no file changes" in job.last_error


def test_create_opens_issue_or_asks(session, settings, tmp_path, monkeypatch) -> None:
    job, _ = create_job(
        session,
        external_key="c1",
        repository="acme/myapp",
        request="redesign landing",
        slack_channel="C1",
        slack_thread_ts="1.2",
        pipeline_phase=PipelinePhase.CREATE,
    )
    session.commit()
    cache = tmp_path / "cache"
    cache.mkdir()

    class Cache:
        path = cache

    monkeypatch.setattr("app.jobs.pipeline.ensure_repo_cache", lambda *a, **k: Cache())

    class ContinueRefine:
        def run(self, context: AgentContext) -> AgentResult:
            return AgentResult(
                ok=True,
                summary="ok",
                structured={
                    "decision": "continue",
                    "title": "Redesign landing",
                    "body": "Do the work",
                    "acceptance_criteria": ["Hero matches spec"],
                    "questions": [],
                },
            )

    slack = FakeSlack()
    deps = WorkerDeps(
        settings=settings,
        slack=slack,
        github=FakeGitHub(),
        coding=FakeCoding(),
        testing=FakeTests(),
        refinement=ContinueRefine(),
    )
    run_claimed_job(session, job, deps, "w1")
    session.refresh(job)
    assert job.status == JobStatus.IDLE.value
    assert job.github_issue_number == 9
    assert job.phase_results["create"]["status"] == "passed"
    created = "\n".join(slack.messages)
    assert "Created ticket #9: Redesign landing" in created
    assert "@devbot implement 9" in created

    job2, _ = create_job(
        session,
        external_key="c2",
        repository="acme/myapp",
        request="vague",
        slack_channel="C1",
        slack_thread_ts="2.2",
        pipeline_phase=PipelinePhase.CREATE,
    )
    session.commit()

    class AskRefine:
        def run(self, context: AgentContext) -> AgentResult:
            return AgentResult(
                ok=True,
                summary="need info",
                structured={"decision": "need_info", "questions": ["Which brand colors?"], "title": "", "body": ""},
            )

    deps.refinement = AskRefine()
    run_claimed_job(session, job2, deps, "w1")
    session.refresh(job2)
    assert job2.status == JobStatus.AWAITING_INPUT.value
    assert job2.github_issue_number is None


def test_create_force_continue_opens_issue(session, settings, tmp_path, monkeypatch) -> None:
    job, _ = create_job(
        session,
        external_key="c-force",
        repository="acme/myapp",
        request="X stats timeout",
        slack_channel="C1",
        slack_thread_ts="3.3",
        pipeline_phase=PipelinePhase.CREATE,
        specification={"force_continue": True, "answers": ["Just create the ticket, no more questions"]},
    )
    session.commit()
    cache = tmp_path / "cache"
    cache.mkdir()

    class Cache:
        path = cache

    monkeypatch.setattr("app.jobs.pipeline.ensure_repo_cache", lambda *a, **k: Cache())

    class AskRefine:
        def run(self, context: AgentContext) -> AgentResult:
            return AgentResult(
                ok=True,
                summary="need info",
                structured={"decision": "need_info", "questions": ["More detail?"], "title": "", "body": ""},
            )

    slack = FakeSlack()
    deps = WorkerDeps(
        settings=settings,
        slack=slack,
        github=FakeGitHub(),
        coding=FakeCoding(),
        testing=FakeTests(),
        refinement=AskRefine(),
    )
    run_claimed_job(session, job, deps, "w1")
    session.refresh(job)
    assert job.status == JobStatus.IDLE.value
    assert job.github_issue_number == 9


def test_force_commit_pushes_agent_local_commits(session, settings, tmp_path, monkeypatch) -> None:
    job = _implement_job(
        session,
        external_key="force-commit",
        request="landscape",
        slack_channel="C1",
        slack_thread_ts="1.2",
    )
    worktree = tmp_path / "wt-commit"
    _init_worktree(worktree)
    job.worktree_path = str(worktree)
    job.branch_name = "agent/issue-9"
    job.status = JobStatus.QUEUED.value
    job.pipeline_phase = PipelinePhase.SECURITY.value
    job.phase_results = {
        "create": {"status": "passed"},
        "implement": {"status": "passed"},
        "test": {"status": "passed"},
        "security": {"status": "passed"},
        "architect": {"status": "pending"},
        "document": {"status": "pending"},
    }
    job.specification = {
        "pending_action": "commit",
        "commit_resume_status": JobStatus.IDLE.value,
        "title": "Landscape",
    }
    job.pull_request_url = "https://github.com/acme/myapp/pull/184"
    job.pull_request_number = 184
    session.commit()

    import subprocess

    (worktree / "rogue.txt").write_text("left by agent", encoding="utf-8")
    subprocess.run(["git", "add", "rogue.txt"], cwd=worktree, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "agent local commit"], cwd=worktree, check=True, capture_output=True)

    pushes: list[str] = []

    def fake_push(self, branch: str):
        from app.git.repository import GitResult

        pushes.append(branch)
        return GitResult(args=["git", "push"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.git.repository.GitRepository.push", fake_push)
    monkeypatch.setattr("app.jobs.pipeline.ensure_repo_cache", lambda *a, **k: None)

    slack = FakeSlack()
    deps = WorkerDeps(settings=settings, slack=slack, github=FakeGitHub(), coding=FakeCoding(), testing=FakeTests())
    run_claimed_job(session, job, deps, "w1")
    session.refresh(job)
    assert pushes == ["agent/issue-9"]
    assert job.status == JobStatus.IDLE.value
    assert (job.specification or {}).get("pending_action") is None
    assert any("Force-commit finished" in msg or "Pushed" in msg for msg in slack.messages)


def test_commit_and_push_always_pushes_even_when_clean(session, settings, tmp_path, monkeypatch) -> None:
    from app.jobs.pipeline import _commit_and_push

    job = _implement_job(session, external_key="clean-push", request="x")
    worktree = tmp_path / "wt-clean"
    _init_worktree(worktree)
    job.worktree_path = str(worktree)
    job.branch_name = "agent/issue-9"
    session.commit()

    pushes: list[str] = []

    def fake_push(self, branch: str):
        from app.git.repository import GitResult

        pushes.append(branch)
        return GitResult(args=["git", "push"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.git.repository.GitRepository.push", fake_push)
    monkeypatch.setattr("app.git.repository.GitRepository.has_changes", lambda self: False)

    deps = WorkerDeps(
        settings=settings, slack=FakeSlack(), github=FakeGitHub(), coding=FakeCoding(), testing=FakeTests()
    )
    result = _commit_and_push(session, job, deps, "w1", reason="security")
    assert result == {"committed": False, "pushed": True}
    assert pushes == ["agent/issue-9"]
    assert (job.specification or {}).get("pushed_sha")


def test_commit_and_push_refuses_missing_worktree(session, settings) -> None:
    import pytest

    from app.jobs.pipeline import PipelineError, _commit_and_push

    job = _implement_job(session, external_key="missing-wt", request="x")
    job.worktree_path = str(settings.agent_workspace / "missing")
    session.commit()
    deps = WorkerDeps(settings=settings, slack=FakeSlack(), github=FakeGitHub(), coding=FakeCoding(), testing=FakeTests())
    with pytest.raises(PipelineError, match="No worktree"):
        _commit_and_push(session, job, deps, "w1", reason="security")


def test_autopilot_queues_the_next_phase(session, settings) -> None:
    from app.jobs.gates import empty_phase_results, mark_phase
    from app.jobs.models import PhaseStatus
    from app.jobs.pipeline import _finish_phase

    job = _implement_job(
        session,
        external_key="auto-1",
        request="x",
        slack_channel="C1",
        slack_thread_ts="1.1",
        specification={"autopilot": True},
    )
    job.phase_results = mark_phase(empty_phase_results(), PipelinePhase.CREATE, PhaseStatus.PASSED)
    job.status = JobStatus.RUNNING.value
    session.commit()
    slack = FakeSlack()
    deps = WorkerDeps(settings=settings, slack=slack, github=FakeGitHub(), coding=FakeCoding(), testing=FakeTests())
    _finish_phase(session, job, deps, PipelinePhase.IMPLEMENT, "done")
    session.refresh(job)
    assert job.status == JobStatus.QUEUED.value
    assert job.pipeline_phase == PipelinePhase.TEST.value
    assert any("Autopilot queued `test`" in msg for msg in slack.messages)
