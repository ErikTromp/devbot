from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.db.models import Job


@dataclass
class AgentContext:
    job: Job
    worktree: Path
    prompt: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    ok: bool
    summary: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    structured: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    name: str

    def run(self, context: AgentContext) -> AgentResult:
        ...
