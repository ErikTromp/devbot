from __future__ import annotations

from app.agents.prompted import PromptedCursorAgent


class ElaborateTestAgent(PromptedCursorAgent):
    name = "elaborate_testing"
    prompt_file = "testing.md"
