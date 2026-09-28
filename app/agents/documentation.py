from __future__ import annotations

from app.agents.prompted import PromptedCursorAgent


class DocumentationAgent(PromptedCursorAgent):
    name = "documentation"
    prompt_file = "documentation.md"
