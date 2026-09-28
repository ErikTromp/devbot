from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.agents.base import AgentContext
from app.agents.cursor import CursorAgent, cursor_cli_args
from app.agents.testing import TestAgent, _js_install_command, detect_test_command
from app.db.models import Job
from app.process import ProcessTimeout, run_process


def test_cursor_command_construction(tmp_path: Path) -> None:
    args = cursor_cli_args("agent", tmp_path, "implement csv", model="composer-2.5", sandbox="enabled")
    assert args[:5] == ["agent", "-p", "--force", "--trust", "--workspace"]
    assert str(tmp_path) in args
    assert "--output-format" in args
    assert "json" in args
    assert "--mode" not in args
    assert args[-1] == "implement csv"
    assert "--model" in args
    assert "--sandbox" in args


def test_cursor_invocation_uses_process_runner(settings, tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        from app.process import ProcessResult

        return ProcessResult(args=args, returncode=0, stdout='{"result":"ok"}', stderr="", duration_seconds=0.2)

    monkeypatch.setattr("app.agents.cursor.run_process", fake_run)
    settings.cursor_api_key = "cursor_test_key"
    job = Job(id=1, external_key="k", repository="acme/myapp", request="x")
    result = CursorAgent(settings).run(AgentContext(job=job, worktree=tmp_path, prompt="do work"))
    assert result.ok
    assert result.structured["result"] == "ok"
    assert captured["args"][0] == "agent"
    assert "--mode" not in captured["args"]
    assert captured["env"]["CURSOR_API_KEY"] == "cursor_test_key"


def test_cursor_plan_mode_flag(tmp_path: Path) -> None:
    args = cursor_cli_args("agent", tmp_path, "plan the work", mode="plan")
    assert args[args.index("--mode") + 1] == "plan"
    assert args[args.index("--output-format") + 1] == "stream-json"


def test_plan_stream_keeps_create_plan_not_status_text() -> None:
    from app.agents.cursor import parse_cursor_stdout

    narration = (
        "Researching the codebase and .devbot/plan.md to draft a concrete implementation plan. "
        "I have enough context to draft the implementation plan. .devbot/plan.md is missing in this worktree."
    )
    plan = "# Landscape\n\nTouch `video.py` and render 16:9 frames.\n\n## Done\n\n- Carousel uses the same ratio.\n"
    events = [
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": narration}]}},
        {
            "type": "tool_call",
            "subtype": "started",
            "call_id": "call_1",
            "tool_call": {"createPlan": {"args": {"plan": plan, "overview": "16:9 for video, image, and carousel", "todos": [{"content": "Update the renderer"}]}}},
        },
        {"type": "result", "subtype": "success", "is_error": False, "result": narration, "session_id": "s1"},
    ]
    stdout = "\n".join(__import__("json").dumps(event) for event in events)
    parsed = parse_cursor_stdout(stdout)
    assert parsed is not None
    assert "# Landscape" in parsed["plan"]
    assert parsed["plan"].startswith("16:9 for video")
    assert "Update the renderer" in parsed["plan"]
    assert "plan.md is missing" not in parsed["plan"]
    assert parsed["result"] == narration


def test_timeout_kills_process() -> None:
    with pytest.raises(ProcessTimeout) as exc:
        run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert exc.value.result.timed_out is True


def test_detect_pytest(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    assert detect_test_command(tmp_path) == ["python", "-m", "pytest", "-q"]
    (tmp_path / "package.json").write_text('{"scripts":{"test":"jest"}}', encoding="utf-8")
    assert detect_test_command(tmp_path)[0] == "npm"


def test_detect_nested_frontend_npm(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"test":"node --test"}}', encoding="utf-8")
    assert detect_test_command(tmp_path) == ["npm", "test", "--silent"]


def test_unmatched_node_glob_is_not_a_test_failure(settings, tmp_path, monkeypatch) -> None:
    from app.process import ProcessResult

    def fake_run(command, **kwargs):
        return ProcessResult(
            args=command,
            returncode=1,
            stdout="",
            stderr="Could not find '/workspace/jobs/DEV-1/worktree/frontend/src/**/*.test.js'\n",
            duration_seconds=0.1,
        )

    monkeypatch.setattr("app.agents.testing.run_process", fake_run)
    monkeypatch.setattr("app.agents.testing.shutil.which", lambda _name: "/usr/bin/npm")
    result = TestAgent(settings).run(
        AgentContext(
            job=Job(id=1, external_key="k", repository="acme/x", request="x"),
            worktree=tmp_path,
            prompt="",
            extra={"command": ["npm", "test", "--silent"]},
        )
    )
    assert result.structured["status"] == "NOT_APPLICABLE"
    assert "not a product failure" in result.summary


def test_missing_test_binary_is_not_applicable(settings, tmp_path) -> None:
    result = TestAgent(settings).run(
        AgentContext(
            job=Job(id=1, external_key="k", repository="acme/x", request="x"),
            worktree=tmp_path,
            prompt="",
            extra={"command": ["devbot-missing-test-runner"]},
        )
    )
    assert result.structured["status"] == "NOT_APPLICABLE"
    assert "devbot-missing-test-runner" in result.summary


def test_js_install_when_node_modules_missing(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert _js_install_command(tmp_path) == ["npm", "install"]
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    assert _js_install_command(tmp_path) == ["npm", "ci"]
    (tmp_path / "node_modules").mkdir()
    assert _js_install_command(tmp_path) is None
