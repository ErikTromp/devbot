from app.config import Settings

HELP_TEXT = """Devbot — GitHub-issue pipeline. You trigger each step; I never merge.

Order: create → implement → test → security → architect → document

*Create a ticket*
`@devbot create web redesign the landing page`
`@devbot create Erik web redesign the landing page` — run create (and later steps) as that person's GitHub + Cursor or Copilot keys
`@devbot web create Erik redesign the landing page` — same; name can sit with the command and repo, not in the description
`@devbot autopilot web redesign the landing page` — create, then run every later step in order
`@devbot autopilot 9` — run the remaining steps for an existing ticket
The coding agent writes a GitHub issue from the request and repo. It asks only if a missing fact makes the ticket unusable. Autopilot pauses if it asks, then continues after you answer. `cancel` turns autopilot off.

*Work the ticket* (use `9` or `#9`, not `DEV-9`)
`@devbot implement 9` — plan mode, then agent implements that plan, tests, PR
`@devbot test 9` — broader tests (Playwright / Docker / Appium if present)
`@devbot security 9` — OWASP-style review and fixes
`@devbot architect 9` — design review
`@devbot document 9` — update docs on the same PR
`@devbot commit 9` — force stage/commit/push of any leftover worktree changes (always available)

Skip a later step: `@devbot test skip 9` (create cannot be skipped)
A step runs only if every earlier step passed or was skipped. Every code step pushes the ticket branch and checks that `origin` has that commit before the step is done. A later step will not create a new branch from `main` once that branch has been published.

*Look around*
`@devbot status` — board (#, title, phase)
`@devbot status 9` · `@devbot retry 9` · `@devbot cancel 9` · `@devbot remove 9` · `@devbot commit 9` · `@devbot ping`

While create is still refining (no GitHub issue yet), use the internal id Slack posts: `@devbot remove DEV-2` / `@devbot status DEV-2`. After `#N` exists, use the GitHub number for implement/test/….

`remove` closes the GitHub issue and PR (if any), deletes the agent branch, drops the local worktree, and deletes the Postgres job. `cancel` only stops the pipeline.

Every step re-reads the Slack thread and the GitHub issue comments (not a Postgres copy). Comment on the issue anytime.

If the agent asks, reply in the thread with `@devbot`. `@devbot just create the ticket` opens GitHub from what it has.

After each finish I post the ticket number, title, and the next `run` or `skip` command.
"""


def format_help(settings: Settings) -> str:
    catalog = settings.repo_catalog()
    users = settings.user_catalog().names()
    user_extra = ""
    if users:
        listed = ", ".join(f"`{name}`" for name in users)
        user_extra = f"\n*Named credentials*\n{listed}\nPut the name only after `create` or `autopilot`: `@devbot create Erik web …`.\n"
    if catalog.entries:
        lines = [f"• `{entry.alias}` → `{entry.repository}`" for entry in catalog.entries]
        extra = (
            "\n*Supported repos*\n"
            + "\n".join(lines)
            + "\nUse the alias: `@devbot create web …` or `@devbot web create …`.\n"
            + user_extra
        )
    elif settings.default_github_org.strip():
        extra = (
            "\n*Supported repos*\n"
            f"No allowlist. Use `owner/name` (short names resolve under `{settings.default_github_org}` only if listed in `ALLOWED_REPOS`).\n"
            + user_extra
        )
    else:
        extra = (
            "\n*Supported repos*\n"
            "No allowlist. Use `owner/name` in the command, or set `ALLOWED_REPOS` aliases.\n"
            + user_extra
        )
    return HELP_TEXT.rstrip() + "\n" + extra
