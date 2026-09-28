from __future__ import annotations

from app.agents.prompted import PromptedCursorAgent


class RefinementAgent(PromptedCursorAgent):
    name = "refinement"
    prompt_file = "refinement.md"
