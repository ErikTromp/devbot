from __future__ import annotations

from app.agents.prompted import PromptedCursorAgent


class SecurityAgent(PromptedCursorAgent):
    name = "security"
    prompt_file = "security.md"
