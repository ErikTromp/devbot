from __future__ import annotations

import json
import re
from typing import Any


_DECISION_KEYS = ("decision", "plan", "acceptance_criteria", "questions", "title", "body")
_NARRATION_MARKERS = (
    "researching the codebase",
    "i have enough context",
    "plan.md is missing",
    "draft a concrete implementation plan",
)


def is_plan_narration(text: str) -> bool:
    """True when the text is a status update, not an implementation plan."""
    body = " ".join((text or "").lower().split())
    if not body:
        return True
    hits = sum(marker in body for marker in _NARRATION_MARKERS)
    return hits >= 2 and len(body) < 1500 and not body.lstrip().startswith("#")


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    for match in re.finditer(r"```(?:json)?\s*", text):
        start = text.find("{", match.end())
        if start < 0:
            continue
        found = _decode_object(decoder, text[start:])
        if found and _looks_like_decision(found):
            return found
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = 0
    while True:
        index = text.find("{", start)
        if index < 0:
            return None
        found = _decode_object(decoder, text[index:])
        if found and _looks_like_decision(found):
            return found
        start = index + 1


def _decode_object(decoder: json.JSONDecoder, text: str) -> dict[str, Any] | None:
    try:
        data, _ = decoder.raw_decode(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _looks_like_decision(data: dict[str, Any]) -> bool:
    return any(key in data for key in _DECISION_KEYS)


def parse_agent_decision(summary: str, structured: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if structured:
        if structured.get("decision") or structured.get("acceptance_criteria") or structured.get("questions"):
            return structured
        for key in ("result", "text", "message"):
            value = structured.get(key)
            if isinstance(value, dict) and (
                value.get("decision") or value.get("acceptance_criteria") or value.get("questions")
            ):
                return value
            if isinstance(value, str):
                found = extract_json_object(value)
                if found and (found.get("decision") or found.get("acceptance_criteria") or found.get("questions")):
                    return found
    if summary:
        found = extract_json_object(summary)
        if found:
            return found
    return None


def needs_more_info(decision: dict[str, Any] | None) -> bool:
    if not decision:
        return False
    if str(decision.get("decision") or "").lower() in {"need_info", "needs_info", "ask"}:
        return True
    questions = decision.get("questions") or []
    return bool(questions) and str(decision.get("decision") or "").lower() not in {"continue", "pass", "done"}


def question_lines(decision: dict[str, Any] | None) -> list[str]:
    if not decision:
        return []
    raw = decision.get("questions") or []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw if str(item).strip()]
