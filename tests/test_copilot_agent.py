from __future__ import annotations

import json
from pathlib import Path

from app.agents.base import AgentContext
from app.agents.copilot import CopilotAgent, copilot_cli_args, parse_copilot_stdout
from app.db.models import Job
from app.process import ProcessResult


def test_copilot_command_construction(tmp_path: Path) -> None:
    args = copilot_cli_args("copilot", tmp_path, "implement csv", model="gpt-5.4")
    assert args[:3] == ["copilot", "-p", "implement csv"]
    assert "--yolo" in args
    assert "--no-ask-user" in args
    assert "--silent" in args
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("-C") + 1] == str(tmp_path)
    assert "--plan" not in args
    assert args[args.index("--model") + 1] == "gpt-5.4"


def test_copilot_plan_mode_flag(tmp_path: Path) -> None:
    args = copilot_cli_args("copilot", tmp_path, "plan the work", mode="plan")
    assert "--plan" in args
    assert args[args.index("--output-format") + 1] == "json"


def test_copilot_invocation_uses_process_runner(settings, tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return ProcessResult(args=args, returncode=0, stdout='{"result":"ok"}', stderr="", duration_seconds=0.2)

    monkeypatch.setattr("app.agents.copilot.run_process", fake_run)
    settings.copilot_github_token = "github_pat_test"
    settings.copilot_cli_bin = "copilot"
    job = Job(id=1, external_key="k", repository="acme/myapp", request="x")
    result = CopilotAgent(settings).run(AgentContext(job=job, worktree=tmp_path, prompt="do work"))
    assert result.ok
    assert result.structured["result"] == "ok"
    assert captured["args"][0] == "copilot"
    assert "--plan" not in captured["args"]
    assert captured["env"]["COPILOT_GITHUB_TOKEN"] == "github_pat_test"
    assert captured["env"]["COPILOT_ALLOW_ALL"] == "true"


def test_parse_copilot_jsonl_keeps_plan_and_decision() -> None:
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "drafting"}]}},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"decision":"continue","plan":"# Touch feature.txt\\n\\nAdd a README line."}',
            "session_id": "s1",
        },
    ]
    parsed = parse_copilot_stdout("\n".join(json.dumps(event) for event in events))
    assert parsed is not None
    assert parsed["decision"] == "continue"
    assert "Touch feature.txt" in parsed["plan"]
    assert parsed["session_id"] == "s1"


def test_copilot_plan_mode_reads_plan_file(settings, tmp_path, monkeypatch) -> None:
    plan_path = tmp_path / ".devbot" / "plan.md"
    plan_path.parent.mkdir()
    plan_path.write_text("# Landscape\n\nTouch feature.txt.\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        return ProcessResult(args=args, returncode=0, stdout='{"result":"Plan written."}', stderr="", duration_seconds=0.1)

    monkeypatch.setattr("app.agents.copilot.run_process", fake_run)
    settings.copilot_github_token = "github_pat_test"
    job = Job(id=1, external_key="k", repository="acme/myapp", request="x")
    result = CopilotAgent(settings).run(
        AgentContext(job=job, worktree=tmp_path, prompt="plan", extra={"mode": "plan"})
    )
    assert result.ok
    assert "# Landscape" in (result.structured or {}).get("plan", "")
