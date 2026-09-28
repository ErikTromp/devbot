from __future__ import annotations

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
    assert resolved.slack is base.slack
    assert deps_for_job(base, None) is base
