from __future__ import annotations

from app.agents.decision import needs_more_info, parse_agent_decision, question_lines
from app.github.issues import issue_body, parse_acceptance_criteria, specification_from_issue
from app.jobs.models import parse_github_issue_number


def test_parse_decision_from_fenced_json() -> None:
    text = 'Here\n```json\n{"decision": "need_info", "questions": ["Scope?"], "title": ""}\n```'
    decision = parse_agent_decision(text)
    assert decision is not None
    assert needs_more_info(decision) is True
    assert question_lines(decision) == ["Scope?"]


def test_parse_plan_json_with_braces_inside_markdown() -> None:
    plan = "# Plan\n\nUse `if (x) { return 1 }` in `src/app.ts`.\n"
    text = 'Status first.\n```json\n{"decision": "continue", "plan": ' + __import__("json").dumps(plan) + ', "questions": []}\n```'
    decision = parse_agent_decision(text)
    assert decision is not None
    assert "return 1" in decision["plan"]
    assert needs_more_info(decision) is False


def test_continue_without_questions() -> None:
    decision = parse_agent_decision("", {"result": '{"decision":"continue","title":"T","acceptance_criteria":["A"]}'})
    assert decision is not None
    assert needs_more_info(decision) is False
    assert decision["title"] == "T"


def test_issue_helpers() -> None:
    body = issue_body(title="T", description="D", acceptance_criteria=["Hero", "CTA"])
    assert parse_acceptance_criteria(body) == ["Hero", "CTA"]
    spec = specification_from_issue({"title": "T", "body": body})
    assert spec["acceptance_criteria"] == ["Hero", "CTA"]


def test_parse_github_issue_number() -> None:
    assert parse_github_issue_number("9") == 9
    assert parse_github_issue_number("#9") == 9
    assert parse_github_issue_number("DEV-9") is None
