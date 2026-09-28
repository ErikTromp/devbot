# Devbot

Self-hosted pipeline that turns a Slack mention into a GitHub issue, a git worktree, and a pull request. Cursor’s headless CLI does the editing. You trigger each step. Devbot never merges.

`create` → `implement` → `test` → `security` → `architect` → `document`

After a ticket exists, Slack commands use the **GitHub issue number** (`9` or `#9`). Use the internal id `DEV-N` only for `remove`, `status`, `cancel`, and `retry` while `create` is still open and no issue exists yet.

## Commands

```text
@devbot create web redesign the landing page
@devbot create Erik web redesign the landing page
@devbot autopilot web redesign the landing page
@devbot autopilot 9          # remaining steps, in order
@devbot implement 9
@devbot test 9
@devbot security 9
@devbot architect 9
@devbot document 9
@devbot commit 9             # flush leftover worktree changes

@devbot test skip 9          # any step except create
@devbot status               # board: issue #, title, phase
@devbot status 9
@devbot retry 9
@devbot cancel 9             # stop the pipeline, keep records
@devbot remove 9             # close issue/PR, delete branch, drop worktree and DB row
@devbot help
@devbot ping
```

`create` is the only command that opens an issue. `autopilot` runs `create` and then every later step, or continues an existing ticket from the next unfinished step. Name the repo by its `ALLOWED_REPOS` alias (`web`) or as `owner/name`. On `create` and on an `autopilot` that creates, an optional name from `users.yaml` selects that person’s GitHub token and Cursor key. Later steps inherit it.

Other commands attach to an existing issue. Add the alias when the issue number exists in more than one repo. A later step runs only when every earlier step is `passed` or `skipped`. Each code step commits dirty files, pushes the ticket branch, and checks that `origin` has the same commit before the step finishes. Once that branch has been published, a later step will not recreate the worktree from the default branch. `commit` flushes stuck work without re-running a phase.

When a step finishes, Slack posts `#N`, the title, and the next `run` or `skip` command. If Cursor is blocked, it asks in the thread. Reply with `@devbot`. Every step re-reads that thread and the GitHub issue comments.

## What each step does

| Step | Result |
| --- | --- |
| `create` | Cursor agent mode writes a GitHub issue. It asks only when a missing fact makes the ticket unusable. |
| `implement` | Worktree on `agent/issue-N`. Plan, then agent. Nested unit tests (`npm test` / pytest under `frontend` or `backend`). Commit, push, open a PR. |
| `test` | Broader checks (Playwright, Docker, Appium when present). Missing environment is not a pass. |
| `security` | OWASP-style review plus scanners that are already in the repo. Fixes stay on the same branch. |
| `architect` | Design review against the repo’s own patterns. |
| `document` | Docs on the same PR. The job completes. You merge. |

The worker also keeps a user-owned GitHub Project titled **Devbot** and moves the issue across Status columns as phases run. The signed-in dashboard at `/` shows the same board. Ticket pages live at `/tickets/DEV-N`.

## Run

Token, Slack, and GitHub setup is in **[SETUP.md](SETUP.md)**.

Required environment: `DATABASE_URL`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `GITHUB_TOKEN` (Contents, pull requests, issues, and Projects), `CURSOR_API_KEY`, `DEFAULT_GITHUB_ORG`, `AGENT_WORKSPACE`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`.

Optional: `BASE_URL` (Slack links to `{BASE_URL}/tickets/DEV-N` when a plan is ready), `GITHUB_PROJECT_ID`, `GITHUB_ASSIGNEE`, `USERS_FILE`, `ALLOWED_REPOS`, `CURSOR_MODEL`.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
copy users.yaml.example users.yaml
docker compose up postgres -d
alembic upgrade head
uvicorn app.main:app --reload --port 8000
python -m app.jobs.worker
```

Or `docker compose up -d --build` (API on port **8100**; the worker image includes the Linux Cursor CLI). On Windows, run the worker on the host so it can use `agent.exe`:

```bash
docker compose up -d postgres api
python -m app.jobs.worker
```

Slack Events API request URL: `https://<host>/slack/events`. Subscribe to `app_mention`. A laptop needs a tunnel.

```bash
pytest
```

Tests do not need Slack, GitHub, or Cursor credentials.

## How it is wired

FastAPI verifies Slack and GitHub webhook signatures and writes Postgres. A worker claims jobs with a lease and `SKIP LOCKED`, then runs Cursor (`agent -p --force --trust --workspace`, and `--mode plan` while planning `implement`), git, tests, and the GitHub API.

Job status moves `QUEUED` → `RUNNING` → `IDLE` | `AWAITING_INPUT` | `FAILED` | `CANCELLED` | `COMPLETED`. A crashed worker’s job can be reclaimed after `JOB_LEASE_SECONDS`.

`ALLOWED_REPOS` is a comma-separated list of `owner/repo;alias` or `owner;alias` (the second form becomes `owner/alias`). Slack uses the alias. An empty allowlist accepts any `owner/name` the token can access.

Dashboard login protects `/`, `/tickets/*`, and job reads. `POST /jobs` stays open so you can enqueue a create-phase job without Slack:

```bash
curl -X POST http://127.0.0.1:8000/jobs -H "Content-Type: application/json" -d "{\"repository\":\"acme/web\",\"request\":\"add a README comment\"}"
```

Do not expose that port on the public internet. Put the API behind a private network or a reverse proxy that is not world-reachable.

## Security

Cursor can edit the worktree and run a shell inside it. Keep the worker environment to `GITHUB_TOKEN` and `CURSOR_API_KEY` (or the per-person keys in `users.yaml`). Slack text is untrusted. Repository names are validated against the allowlist. A git diff and the test runner are the check, not the model’s claim that it is done. Webhooks use Slack and GitHub HMAC.
