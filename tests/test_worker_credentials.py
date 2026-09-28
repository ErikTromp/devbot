from __future__ import annotations

from app.agents.copilot import CopilotAgent
from app.agents.cursor import CursorAgent
from app.jobs.worker import build_deps, deps_for_job


def test_deps_for_job_overlays_named_tokens(settings) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Erik\n    github_token: ghp_erik\n    cursor_api_key: cursor_erik\n",
        encoding="utf-8",
    )
    base = build_deps(settings)
    resolved = deps_for_job(base, "Erik")
    assert resolved.settings.github_token == "ghp_erik"
    assert resolved.settings.cursor_api_key == "cursor_erik"
    assert resolved.settings.coding_agent == "cursor"
    assert isinstance(resolved.coding.cursor, CursorAgent)
    assert resolved.slack is base.slack
    assert deps_for_job(base, None) is base


def test_deps_for_job_overlays_copilot_tokens(settings) -> None:
    settings.users_file.write_text(
        "users:\n  - name: Ada\n    github_token: ghp_ada\n    copilot_github_token: github_pat_ada\n",
        encoding="utf-8",
    )
    base = build_deps(settings)
    resolved = deps_for_job(base, "Ada")
    assert resolved.settings.github_token == "ghp_ada"
    assert resolved.settings.copilot_github_token == "github_pat_ada"
    assert resolved.settings.resolved_coding_agent() == "copilot"
    assert isinstance(resolved.coding.cursor, CopilotAgent)
    assert resolved.slack is base.slack
