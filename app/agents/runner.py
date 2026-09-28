from __future__ import annotations

from app.agents.copilot import CopilotAgent
from app.agents.cursor import CursorAgent
from app.config import Settings


def coding_runner(settings: Settings) -> CursorAgent | CopilotAgent:
    if settings.resolved_coding_agent() == "copilot":
        return CopilotAgent(settings)
    return CursorAgent(settings)
