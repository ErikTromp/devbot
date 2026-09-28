from __future__ import annotations

from pathlib import Path

from app.jobs.board import board_phase, render_plan_markdown
from app.jobs.gates import empty_phase_results, mark_phase
from app.jobs.models import JobStatus, PhaseStatus, PipelinePhase
from app.jobs.service import create_job


def test_empty_board(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "No pipeline jobs yet" in response.text
    assert "text/html" in response.headers["content-type"]


def test_board_and_ticket_detail(client, session) -> None:
    job, _ = create_job(
        session,
        external_key="dash-1",
        repository="acme/myapp",
        request="Fix login timeout",
        specification={"title": "Fix login timeout"},
        github_issue_number=12,
        github_issue_url="https://github.com/acme/myapp/issues/12",
    )
    session.commit()
    board = client.get("/")
    assert board.status_code == 200
    assert "Fix login timeout" in board.text
    assert "#12" in board.text
    assert 'href="/tickets/DEV-1"' in board.text

    detail = client.get(f"/tickets/{job.display_id}")
    assert detail.status_code == 200
    assert "Fix login timeout" in detail.text
    assert "No plan yet" in detail.text
    assert "Job created" in detail.text


def test_running_ticket_shows_activity(client, session) -> None:
    job, _ = create_job(
        session,
        external_key="dash-run",
        repository="acme/myapp",
        request="Landscape",
        specification={"title": "Landscape"},
        github_issue_number=49,
        pipeline_phase=PipelinePhase.IMPLEMENT,
    )
    job.status = JobStatus.RUNNING.value
    job.current_stage = "planning"
    session.commit()

    detail = client.get(f"/tickets/{job.display_id}")
    assert detail.status_code == 200
    assert 'data-status="running"' in detail.text
    assert "Writing the implementation plan" in detail.text
    assert "location.reload" in detail.text
    assert "implement pending" not in detail.text.lower()

    board = client.get("/")
    assert 'data-status="running"' in board.text


def test_unknown_ticket_is_html_404(client) -> None:
    response = client.get("/tickets/DEV-99")
    assert response.status_code == 404
    assert "Ticket not found" in response.text


def test_list_jobs_and_plan_fallback(client, session) -> None:
    job, _ = create_job(
        session,
        external_key="dash-2",
        repository="acme/myapp",
        request="Export CSV",
        specification={
            "title": "Export CSV",
            "implementation_plan_path": ".devbot/plan.md",
            "implementation_plan_summary": "Write the exporter.",
        },
    )
    job.status = JobStatus.IDLE.value
    job.pipeline_phase = PipelinePhase.IMPLEMENT.value
    job.phase_results = mark_phase(empty_phase_results(), PipelinePhase.CREATE, PhaseStatus.PASSED)
    session.commit()

    listing = client.get("/jobs")
    assert listing.status_code == 200
    body = listing.json()
    assert body["counts"]["IDLE"] == 1
    payload = body["jobs"][0]
    assert payload["display_id"] == job.display_id
    assert payload["board_phase"] == "implement"
    assert payload["specification"]["implementation_plan_summary"] == "Write the exporter."
    assert set(payload["specification"]) == {
        "title",
        "implementation_plan_path",
        "implementation_plan_summary",
    }

    plan = client.get(f"/jobs/{job.display_id}/plan")
    assert plan.status_code == 200
    assert plan.json()["source"] == "summary"
    assert "Write the exporter" in plan.json()["markdown"]


def test_plan_reads_worktree_file(client, session, tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    plan_file = worktree / ".devbot" / "plan.md"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text("# Plan\n\nTouch **feature.txt**.\n", encoding="utf-8")
    job, _ = create_job(
        session,
        external_key="dash-3",
        repository="acme/myapp",
        request="Touch feature",
        specification={
            "title": "Touch feature",
            "implementation_plan_path": ".devbot/plan.md",
            "implementation_plan_summary": "Short summary only.",
        },
    )
    job.worktree_path = str(worktree)
    session.commit()

    plan = client.get(f"/jobs/{job.id}/plan")
    assert plan.json()["source"] == "worktree"
    assert "Touch **feature.txt**" in plan.json()["markdown"]

    detail = client.get(f"/tickets/{job.id}")
    assert "<strong>feature.txt</strong>" in detail.text
    assert "Short summary only." not in detail.text


def test_plan_hides_status_narration(client, session, tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    plan_file = worktree / ".devbot" / "plan.md"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text(
        "Researching the codebase and .devbot/plan.md to draft a concrete implementation plan.\n\n"
        "I have enough context to draft the implementation plan. .devbot/plan.md is missing in this worktree.\n",
        encoding="utf-8",
    )
    job, _ = create_job(
        session,
        external_key="dash-narration",
        repository="acme/myapp",
        request="Landscape",
        specification={
            "title": "Landscape",
            "implementation_plan_path": ".devbot/plan.md",
            "implementation_plan_summary": "Researching the codebase and .devbot/plan.md to draft a concrete implementation plan.",
            "implementation_plan": "# Landscape\n\nRender 16:9 for video, image, and carousel.\n",
        },
    )
    job.worktree_path = str(worktree)
    session.commit()

    detail = client.get(f"/tickets/{job.display_id}")
    assert "Saved on the ticket" in detail.text
    assert "Render 16:9" in detail.text
    assert "plan.md is missing" not in detail.text


def test_board_phase_and_markdown_safety(session) -> None:
    job, _ = create_job(session, external_key="dash-4", repository="acme/myapp", request="x")
    job.status = JobStatus.FAILED.value
    job.pipeline_phase = PipelinePhase.TEST.value
    session.flush()
    assert board_phase(job) == "test"

    job.status = JobStatus.COMPLETED.value
    session.flush()
    assert board_phase(job) == "document"

    html = render_plan_markdown("<script>alert(1)</script>\n\n**ok**")
    assert "<script>" not in html
    assert "alert(1)" not in html
    assert "<strong>ok</strong>" in html

    rich = render_plan_markdown("| Col | Val |\n| --- | --- |\n| a | b |\n\n- first\n- second\n")
    assert "<table>" in rich
    assert "<th>" in rich
    assert "<li>" in rich
    assert "first" in rich


def test_board_shows_credential_owner(client, session) -> None:
    create_job(
        session,
        external_key="dash-owner",
        repository="acme/myapp",
        request="Owned work",
        credential_user="Erik",
        specification={"title": "Owned work"},
    )
    session.commit()
    board = client.get("/")
    assert "Owned work" in board.text
    assert "Erik" in board.text
    assert "owner" in board.text
    detail = client.get("/tickets/DEV-1")
    assert "Owner Erik" in detail.text


def test_board_filters_by_repo(client, session) -> None:
    create_job(session, external_key="dash-a", repository="acme/myapp", request="App work", specification={"title": "App work"})
    create_job(session, external_key="dash-b", repository="acme/other", request="Other work", specification={"title": "Other work"})
    session.commit()
    board = client.get("/")
    assert "App work" in board.text
    assert "Other work" in board.text
    assert 'id="repo-filter"' in board.text
    filtered = client.get("/?repo=acme/other")
    assert "Other work" in filtered.text
    assert "App work" not in filtered.text
    assert 'selected' in filtered.text
