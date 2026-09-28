from __future__ import annotations

import re
from typing import Any


def issue_body(*, title: str, description: str, acceptance_criteria: list[str], extra: str = "") -> str:
    criteria = "\n".join(f"- [ ] {item}" for item in acceptance_criteria) or "- [ ] Request implemented as specified"
    parts = [
        f"## Summary\n\n{description or title}",
        f"## Acceptance Criteria\n\n{criteria}",
    ]
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) + "\n"


def parse_acceptance_criteria(body: str | None) -> list[str]:
    if not body:
        return []
    lines: list[str] = []
    in_section = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.lower().startswith("## acceptance"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        checked = re.match(r"- \[[ xX]\]\s+(.+)", line)
        if checked:
            lines.append(checked.group(1).strip())
        elif line.startswith("- "):
            lines.append(line[2:].strip())
    return lines


def specification_from_issue(issue: dict[str, Any]) -> dict[str, Any]:
    body = issue.get("body") or ""
    return {
        "title": issue.get("title") or "",
        "description": body,
        "acceptance_criteria": parse_acceptance_criteria(body),
        "imported": True,
    }
