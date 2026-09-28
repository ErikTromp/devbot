from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agents.base import AgentContext, AgentResult
from app.agents.decision import extract_json_object, is_plan_narration
from app.config import Settings
from app.process import ProcessTimeout, run_process

logger = logging.getLogger(__name__)


def cursor_cli_args(
    binary: str,
    workspace: Path,
    prompt: str,
    *,
    model: str = "",
    sandbox: str = "",
    mode: str = "agent",
) -> list[str]:
    args = [
        binary,
        "-p",
        "--force",
        "--trust",
        "--workspace",
        str(workspace),
        "--output-format",
        "stream-json" if mode == "plan" else "json",
    ]
    if mode and mode not in {"agent", "default", ""}:
        args.extend(["--mode", mode])
    if model:
        args.extend(["--model", model])
    if sandbox:
        args.extend(["--sandbox", sandbox])
    args.append(prompt)
    return args


class CursorAgent:
    name = "cursor"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, context: AgentContext) -> AgentResult:
        mode = str(context.extra.get("mode") or "agent")
        args = cursor_cli_args(
            self.settings.cursor_cli_bin,
            context.worktree,
            context.prompt,
            model=self.settings.cursor_model,
            sandbox=self.settings.cursor_sandbox,
            mode=mode,
        )
        env = {}
        if self.settings.cursor_api_key:
            env["CURSOR_API_KEY"] = self.settings.cursor_api_key
        try:
            result = run_process(
                args,
                cwd=context.worktree,
                env=env,
                timeout=self.settings.cursor_timeout_seconds,
            )
        except ProcessTimeout as exc:
            return AgentResult(
                ok=False,
                summary="Cursor CLI timed out",
                stdout=exc.result.stdout[-8000:],
                stderr=exc.result.stderr[-8000:],
                exit_code=exc.result.returncode,
                duration_seconds=exc.result.duration_seconds,
                metadata={"timed_out": True, "argv0": args[0]},
            )
        structured = parse_cursor_stdout(result.stdout)
        ok = result.returncode == 0 and not bool((structured or {}).get("is_error"))
        summary = ""
        if structured:
            summary = str(structured.get("plan") or structured.get("result") or structured.get("text") or structured.get("message") or "")
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


def parse_cursor_stdout(stdout: str) -> dict | None:
    """Parse Cursor `--output-format json` or plan-mode `stream-json`.

    Plan mode's real plan is in the `createPlan` tool args. The final `result`
    text is often only a status sentence, so stream-json is required to keep it.
    """
    events = _json_events(stdout)
    if not events:
        return _parse_json(stdout)
    if len(events) == 1 and events[0].get("type") != "tool_call":
        data = dict(events[0])
        plan = _plan_from_events(events) or _plan_from_decision(str(data.get("result") or ""))
        if plan:
            data["plan"] = plan
        return data
    reduced = _reduce_stream(events)
    return reduced or None


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


def _reduce_stream(events: list[dict]) -> dict:
    result_text = ""
    is_error = False
    session_id = ""
    for event in events:
        if event.get("type") != "result":
            continue
        if isinstance(event.get("result"), str):
            result_text = event["result"]
        is_error = bool(event.get("is_error")) or event.get("subtype") not in {None, "success"}
        session_id = str(event.get("session_id") or session_id)
    plan = _plan_from_events(events) or _plan_from_decision(result_text)
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


def _plan_from_decision(text: str) -> str:
    decision = extract_json_object(text) or {}
    plan = decision.get("plan")
    if isinstance(plan, str) and plan.strip() and not is_plan_narration(plan):
        return plan.strip()
    return ""


def _plan_from_events(events: list[dict]) -> str:
    plans: list[str] = []
    for event in events:
        if event.get("type") != "tool_call":
            continue
        name, args, result = _tool_parts(event)
        key = name.lower().replace("_", "")
        if key in {"createplan", "createplantoolcall"}:
            text = _inline_plan(args) or _inline_plan(result if isinstance(result, dict) else {})
            if not text and isinstance(result, dict):
                uri = str(result.get("planUri") or result.get("plan_uri") or "").strip()
                text = _read_plan_uri(uri)
            if text and not is_plan_narration(text):
                plans.append(text)
        path = str(args.get("path") or args.get("file_path") or "").replace("\\", "/")
        file_text = args.get("fileText") or args.get("contents") or args.get("content")
        if path.endswith(".devbot/plan.md") and isinstance(file_text, str) and file_text.strip():
            if not is_plan_narration(file_text):
                plans.append(file_text.strip())
    return plans[-1] if plans else ""


def _tool_parts(event: dict) -> tuple[str, dict, dict]:
    tool_call = event.get("tool_call") or {}
    if not isinstance(tool_call, dict):
        return "", {}, {}
    function = tool_call.get("function")
    if isinstance(function, dict):
        raw_args = function.get("arguments")
        args = raw_args if isinstance(raw_args, dict) else {}
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
                args = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                args = {}
        return str(function.get("name") or ""), args, {}
    for key, value in tool_call.items():
        if not isinstance(value, dict):
            continue
        args = value.get("args") if isinstance(value.get("args"), dict) else {}
        result = value.get("result") if isinstance(value.get("result"), dict) else {}
        name = key[: -len("ToolCall")] if key.endswith("ToolCall") else key
        return name, args, result
    return "", {}, {}


def _inline_plan(args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    plan = args.get("plan") if isinstance(args.get("plan"), str) else ""
    success = args.get("success")
    if not plan.strip() and isinstance(success, dict):
        return _inline_plan(success)
    if not plan.strip() or is_plan_narration(plan):
        return ""
    parts = []
    overview = args.get("overview")
    if isinstance(overview, str) and overview.strip() and overview.strip() not in plan:
        parts.append(overview.strip())
    parts.append(plan.strip())
    todos = args.get("todos") if isinstance(args.get("todos"), list) else []
    items = [
        f"- {item['content'].strip()}"
        for item in todos
        if isinstance(item, dict) and isinstance(item.get("content"), str) and item["content"].strip()
    ]
    if items:
        parts.append("\n".join(items))
    return "\n\n".join(parts)


def _read_plan_uri(uri: str) -> str:
    if not uri or "://" in uri:
        return ""
    try:
        return Path(uri).read_text(encoding="utf-8")
    except OSError:
        return ""


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
