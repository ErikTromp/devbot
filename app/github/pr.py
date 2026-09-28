from __future__ import annotations

from typing import Any

from app.db.models import Job


def build_pr_body(job: Job, *, diff_stat: str, test_summary: str, agent_verification: str, risks: list[str] | None = None) -> str:
    spec = job.specification or {}
    criteria = spec.get("acceptance_criteria") or []
    criteria_lines = "\n".join(f"- [ ] {item}" for item in criteria) if criteria else "- [ ] Request implemented as specified in Slack"
    notes = "\n".join(f"- {item}" for item in (risks or spec.get("risks") or [])) or "- None recorded"
    ticket = f"{job.issue_ref}" + (f" ({job.github_issue_url})" if job.github_issue_url else "")
    return (
        f"## Summary\n\n{job.request}\n\n"
        f"Ticket: {ticket}\n"
        f"Branch: `{job.branch_name}`\n\n"
        f"## Acceptance Criteria\n\n{criteria_lines}\n\n"
        f"## Tests\n\n{test_summary}\n\n"
        f"## Agent Verification\n\n{agent_verification}\n\n"
        f"```\n{diff_stat.strip() or 'No diffstat available.'}\n```\n\n"
        f"## Risks / Notes\n\n{notes}\n"
    )


def pr_title(job: Job) -> str:
    spec: dict[str, Any] = job.specification or {}
    title = spec.get("title") or job.request.strip().split("\n", 1)[0]
    return f"{job.issue_ref}: {title[:80]}"
