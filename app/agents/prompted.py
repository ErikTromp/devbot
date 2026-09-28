from __future__ import annotations

from app.agents.base import AgentContext, AgentResult
from app.agents.git_rules import AGENT_GIT_RULES
from app.agents.coding import load_prompt
from app.agents.cursor import CursorAgent
from app.agents.decision import parse_agent_decision
from app.agents.runner import coding_runner
from app.config import Settings
from app.db.models import Job


def build_ticket_prompt(job: Job, template: str, extra: str = "") -> str:
    spec = job.specification or {}
    criteria = spec.get("acceptance_criteria") or []
    criteria_text = "\n".join(f"- {item}" for item in criteria) or "- (none listed)"
    answers = spec.get("answers") or []
    answers_text = "\n".join(f"- {item}" for item in answers) if answers else "- (none)"
    extra_bits = extra
    extra_bits += (
        f"\n{AGENT_GIT_RULES}\n"
        "\nAsk the human only if you are blocked on a fact that is not in the repo, "
        "Slack thread, or GitHub comments. Prefer assumptions in the ticket/plan over questions. "
        "Do not invoke brainstorm or interview skills.\n"
    )
    if spec.get("force_continue"):
        extra_bits += (
            "\nThe user told you to stop asking and create the ticket now. "
            "You MUST set decision to continue. Write the best ticket from the request and clarifications.\n"
        )
    elif answers:
        extra_bits += (
            "\nThe user already answered in Slack. Do not ask another round. "
            "Set decision to continue and write the ticket from what you have.\n"
        )
    return (
        f"{template.strip()}\n\n"
        f"# Ticket\n"
        f"Issue: {job.issue_ref}\n"
        f"Repository: {job.repository}\n"
        f"Branch: {job.branch_name or '(none yet)'}\n"
        f"PR: {job.pull_request_url or '(none yet)'}\n\n"
        f"# Request\n{job.request}\n\n"
        f"# Acceptance criteria\n{criteria_text}\n\n"
        f"# Clarifications from Slack\n{answers_text}\n\n"
        f"# Specification JSON\n{spec or '{}'}\n"
        f"{extra_bits}"
    )


class PromptedCursorAgent:
    name = "prompted"
    prompt_file = ""

    def __init__(self, settings: Settings, cursor: CursorAgent | None = None) -> None:
        self.settings = settings
        self.cursor = cursor or coding_runner(settings)

    def run(self, context: AgentContext) -> AgentResult:
        template = load_prompt(self.settings.prompts_dir, self.prompt_file)
        spec_extra = ""
        conversation = str(context.extra.get("conversation") or "").strip()
        if conversation:
            spec_extra = f"\n# Slack and GitHub conversation\n{conversation}\n"
        prompt = context.prompt or build_ticket_prompt(context.job, template, spec_extra)
        extra = {**context.extra, "mode": context.extra.get("mode") or "agent"}
        result = self.cursor.run(AgentContext(job=context.job, worktree=context.worktree, prompt=prompt, extra=extra))
        decision = parse_agent_decision(result.summary, result.structured)
        if decision:
            result.structured = {**(result.structured or {}), **decision}
        return result
