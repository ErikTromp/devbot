from __future__ import annotations

from app.agents.prompted import PromptedCursorAgent


class ArchitectureAgent(PromptedCursorAgent):
    name = "architecture"
    prompt_file = "architecture.md"
