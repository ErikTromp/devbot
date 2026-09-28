from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agents.base import AgentContext, AgentResult
from app.agents.decision import extract_json_object, is_plan_narration
from app.config import Settings
from app.process import ProcessTimeout, run_process

logger = logging.getLogger(__name__)


def copilot_cli_args(
    binary: str,
    workspace: Path,
    prompt: str,
    *,
    model: str = "",
    mode: str = "agent",
) -> list[str]:
    args = [
        binary,
        "-p",
        prompt,
        "--yolo",
        "--no-ask-user",
        "--silent",
        "--output-format",
        "json",
        "-C",
        str(workspace),
    ]
    if mode == "plan":
        args.append("--plan")
    if model:
        args.extend(["--model", model])
    return args


class CopilotAgent:
    name = "copilot"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, context: AgentContext) -> AgentResult:
        mode = str(context.extra.get("mode") or "agent")
        args = copilot_cli_args(
            self.settings.copilot_cli_bin,
            context.worktree,
            context.prompt,
            model=self.settings.copilot_model,
            mode=mode,
        )
        env = {"COPILOT_ALLOW_ALL": "true"}
        if self.settings.copilot_github_token:
            env["COPILOT_GITHUB_TOKEN"] = self.settings.copilot_github_token
        try:
            result = run_process(
                args,
                cwd=context.worktree,
                env=env,
                timeout=self.settings.copilot_timeout_seconds,
            )
        except ProcessTimeout as exc:
            return AgentResult(
                ok=False,
                summary="Copilot CLI timed out",
                stdout=exc.result.stdout[-8000:],
                stderr=exc.result.stderr[-8000:],
                exit_code=exc.result.returncode,
                duration_seconds=exc.result.duration_seconds,
                metadata={"timed_out": True, "argv0": args[0]},
            )
        structured = parse_copilot_stdout(result.stdout)
        if mode == "plan":
            structured = _attach_plan_file(structured, context.worktree)
        ok = result.returncode == 0 and not bool((structured or {}).get("is_error"))
        summary = ""
        if structured:
            summary = str(
                structured.get("plan") or structured.get("result") or structured.get("text") or structured.get("message") or ""
            )
        if not summary:
            summary = (result.stdout or result.stderr or "")[-2000:]
        return AgentResult(
            ok=ok,
            summary=summary[:4000],
            stdout=result.stdout[-8000:],
            stderr=result.stderr[-8000:],
            exit_code=result.returncode,
            duration_seconds=result.duration_seconds,
            structured=structured,
            metadata={"argv0": args[0], "mode": mode},
        )


def parse_copilot_stdout(stdout: str) -> dict | None:
    events = _json_events(stdout)
    if not events:
        return _parse_json(stdout)
    result_text = ""
    is_error = False
    session_id = ""
    plan = ""
    for event in events:
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("plan"), str) and event["plan"].strip() and not is_plan_narration(event["plan"]):
            plan = event["plan"].strip()
        if event.get("type") == "result" or (event.get("type") is None and "result" in event):
            if isinstance(event.get("result"), str):
                result_text = event["result"]
            is_error = bool(event.get("is_error")) or event.get("subtype") not in {None, "success"}
            session_id = str(event.get("session_id") or session_id)
            continue
        message = _message_text(event)
        if message and event.get("type") in {"assistant", "message", "agent"}:
            result_text = message
    data: dict = {"result": result_text, "is_error": is_error, "session_id": session_id}
    decision = extract_json_object(result_text) or {}
    for key in ("decision", "questions", "title", "body", "acceptance_criteria"):
        if key in decision:
            data[key] = decision[key]
    if plan:
        data["plan"] = plan
    elif isinstance(decision.get("plan"), str) and not is_plan_narration(decision["plan"]):
        data["plan"] = decision["plan"]
    return data


def _attach_plan_file(structured: dict | None, worktree: Path) -> dict | None:
    path = worktree / ".devbot" / "plan.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return structured
    if not text or is_plan_narration(text):
        return structured
    data = dict(structured or {})
    if not str(data.get("plan") or "").strip() or is_plan_narration(str(data.get("plan") or "")):
        data["plan"] = text
    return data


def _message_text(event: dict) -> str:
    message = event.get("message")
    if isinstance(message, str) and message.strip():
        return message
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            text = "\n".join(part for part in parts if part.strip())
            if text.strip():
                return text
    if isinstance(event.get("text"), str) and event["text"].strip():
        return event["text"]
    return ""


def _json_events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"result": data}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None
