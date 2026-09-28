from __future__ import annotations

from pathlib import Path

from app.agents.base import AgentContext, AgentResult
from app.agents.git_rules import AGENT_GIT_RULES
from app.agents.cursor import CursorAgent
from app.agents.decision import parse_agent_decision
from app.agents.runner import coding_runner
from app.config import Settings
from app.db.models import Job


def load_prompt(prompts_dir: Path, name: str) -> str:
    path = prompts_dir / name
    return path.read_text(encoding="utf-8")


def build_coding_prompt(job: Job, template: str, extra: str = "") -> str:
    spec = job.specification or {}
    criteria = spec.get("acceptance_criteria") or []
    criteria_text = "\n".join(f"- {item}" for item in criteria) or "- (none listed)"
    plan_path = str(spec.get("implementation_plan_path") or ".devbot/plan.md")
    plan_summary = str(spec.get("implementation_plan_summary") or spec.get("implementation_plan") or "").strip()
    plan_text = (
        f"Read `{plan_path}` in this worktree and follow it. Do not ask anyone to paste it.\n"
        f"Summary: {plan_summary or '(open the file)'}"
    )
    slim_spec = {key: value for key, value in spec.items() if key != "implementation_plan"}
    return (
        f"{template.strip()}\n\n"
        f"# Ticket\n"
        f"Issue: {job.issue_ref}\n"
        f"Repository: {job.repository}\n"
        f"Branch: {job.branch_name}\n\n"
        f"# Request\n{job.request}\n\n"
        f"# Acceptance criteria\n{criteria_text}\n\n"
        f"# Implementation plan\n{plan_text}\n\n"
        f"# Specification JSON\n{slim_spec or '{}'}\n"
        f"{extra}"
    )


class CodingAgent:
    name = "coding"

    def __init__(self, settings: Settings, cursor: CursorAgent | None = None) -> None:
        self.settings = settings
        self.cursor = cursor or coding_runner(settings)

    def plan(self, context: AgentContext) -> AgentResult:
        return self._run_prompted(context, "planning.md", mode="plan")

    def run(self, context: AgentContext) -> AgentResult:
        return self._run_prompted(context, "coding.md", mode="agent")

    def _run_prompted(self, context: AgentContext, prompt_file: str, *, mode: str) -> AgentResult:
        template = load_prompt(self.settings.prompts_dir, prompt_file)
        extra = ""
        conversation = str(context.extra.get("conversation") or "").strip()
        if conversation:
            extra = f"\n# Slack and GitHub conversation\n{conversation}\n"
        extra += (
            f"\n{AGENT_GIT_RULES}\n"
            "\nAsk the human only if you are blocked on a fact that is not in the repo, "
            "Slack thread, or GitHub comments. Do not invoke brainstorm or interview skills.\n"
        )
        prompt = context.prompt or build_coding_prompt(context.job, template, extra)
        result = self.cursor.run(
            AgentContext(job=context.job, worktree=context.worktree, prompt=prompt, extra={**context.extra, "mode": mode})
        )
        decision = parse_agent_decision(result.summary, result.structured)
        if decision:
            result.structured = {**(result.structured or {}), **decision}
        return result
