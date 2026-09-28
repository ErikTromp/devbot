# Setup

Do secrets before Docker. The worker clones target repos into `AGENT_WORKSPACE`; you do not clone them yourself.

1. Copy `.env`, install Cursor CLI, create GitHub + Slack tokens.
2. Start Postgres / API / worker.
3. Point Slack Events at `https://<host>/slack/events` and `/invite @devbot`.

## 1. `.env`

```powershell
copy .env.example .env
copy users.yaml.example users.yaml
mkdir agent-workspace
```

Linux/macOS: `cp .env.example .env`, `cp users.yaml.example users.yaml`, and `mkdir -p /srv/ai-dev`.

Minimum:

```text
DATABASE_URL=postgresql+psycopg://devbot:devbot@localhost:5432/devbot
DEFAULT_GITHUB_ORG=your-user-or-org
DEFAULT_GITHUB_BRANCH=main
AGENT_WORKSPACE=./agent-workspace
ALLOWED_REPOS=acme/web;web,northwind;shop
```

`ALLOWED_REPOS` entries are `owner/repo;alias` or `owner;alias` (that becomes `owner/alias`). Slack: `@devbot create web …`. Empty allowlist accepts any `owner/name` the token can access. Never commit `.env` or `users.yaml`.

Set `BASE_URL` to the public dashboard origin (no trailing slash), for example `https://devbot.example.com`. After a plan is written, Slack posts `{BASE_URL}/tickets/DEV-N`.

First job for `acme/myapp` writes `AGENT_WORKSPACE/repos/acme__myapp` and `jobs/DEV-1/worktree` on branch `agent/issue-N`.

## 2. Cursor

Headless CLI only (not desktop chat, not Cloud Agents).

```powershell
irm 'https://cursor.com/install?win32=true' | iex
agent --version
```

Linux/macOS: `curl https://cursor.com/install -fsS | bash`. If `agent` is not on `PATH`, set `CURSOR_CLI_BIN` (Windows: often `%USERPROFILE%\.local\bin\agent.exe`).

Create a key at [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations):

```text
CURSOR_API_KEY=cursor_...
```

No `agent login` when the key is set. Smoke-test before Docker:

```powershell
agent -p --trust "Reply with the word pong only"
```

Optional: `CURSOR_MODEL`, `CURSOR_TIMEOUT_SECONDS` (default 900).

## 3. GitHub

Token user must be able to **create issues**, **push** `agent/issue-*`, and **open PRs**.

Fine-grained PAT on those repos:

- **Contents:** read/write
- **Pull requests:** read/write
- **Issues:** read/write (create, comment, labels, assignee)
- **Projects:** read/write (create the Devbot board and move cards)

Classic PAT: `repo` plus `project`. Authorize SAML SSO for orgs or clones return 404.

The worker finds or creates a user-owned Project V2 titled **Devbot**, adds Status options for each pipeline phase plus `done`, and moves the issue when a phase starts or finishes. Pin an existing project with `GITHUB_PROJECT_ID=PVT_...` if the token cannot create projects. Missing project scope is logged and does not fail the job.

Named GitHub + Cursor keys live in `users.yaml` (copy `users.yaml.example`; the file is gitignored):

```yaml
users:
  - name: Erik
    github_token: ghp_...
    cursor_api_key: cursor_...
```

Slack: `@devbot create Erik web Some ticket description`. Later steps inherit that name. Omit the name to use `GITHUB_TOKEN` and `CURSOR_API_KEY`.

```text
GITHUB_TOKEN=github_pat_...
GITHUB_ASSIGNEE=                    # optional; assigned when a phase starts
GITHUB_PROJECT_ID=                  # optional PVT_... node id
USERS_FILE=./users.yaml
```

Skip **Workflows** write unless the agent edits `.github/workflows`. Skip **Actions** write unless you call `workflow_dispatch`. Pushing a branch already runs existing PR/push workflows.

Someone else’s **personal** repo → classic `repo` token. One token cannot span two fine-grained resource owners; use classic `repo` if you mix “repos I own” and “someone else’s personal repos”.

`DEFAULT_GITHUB_BRANCH` must match the remote (`main` vs `master`) or worktrees fail.

GitHub webhook (`POST /github/events`) is optional. Slack pipeline does not need it.

## 4. Slack

Events API over HTTPS — not Incoming Webhooks, not Socket Mode.

[Create an app](https://api.slack.com/apps) → From scratch. Bot scopes:

| Scope | Why |
| --- | --- |
| `app_mentions:read` | `@devbot …` |
| `chat:write` | Progress in the thread |
| `channels:history` | Re-read the Slack thread on every pipeline step |
| `groups:history` | Same, for private channels |

Reinstall the Slack app after adding history scopes. Without them, Cursor still runs but cannot see older thread messages (GitHub issue comments still load).

Install → `SLACK_BOT_TOKEN` (`xoxb-…`). **Basic Information → Signing Secret** → `SLACK_SIGNING_SECRET`.

**Socket Mode: Off.** Event Subscriptions → Enable → Request URL `https://<host>/slack/events` (not `/health`) → subscribe to **`app_mention`** → Save. Reinstall if Slack asks.

Laptop: start the API, then a tunnel at the API port (**8100** Compose, **8000** host uvicorn):

```powershell
ngrok http 8100
```

Request URL is `https://<tunnel>/slack/events`. After every ngrok restart, paste the new host and Retry. Server: `https://your.domain/slack/events`.

```text
/invite @devbot
```

```text
@devbot create web add a one-line comment in the README
@devbot implement 9
@devbot test 9
@devbot security 9
@devbot architect 9
@devbot document 9
@devbot test skip 9
@devbot status
@devbot remove 9
@devbot help
```

Issue numbers only (`9` / `#9`). `retry 9`, `cancel 9`, `remove 9`, `status 9`. After create, Slack posts `Created ticket #N: title`. After each step it tells you the next `run` or `skip`. `remove` closes the GitHub issue/PR, deletes the agent branch, and drops the local worktree and Postgres row.

Smoke: `GET /health` with `slack_token_configured: true`, then `@devbot ping`. Silence = URL not verified, no `app_mention`, bot not invited, or tunnel pointing at the wrong port.

## 5. Start

```powershell
docker compose up -d --build
docker compose logs -f worker
```

`GET http://127.0.0.1:8100/health` → `{"status":"ok"}`. Then verify Slack’s Event URL.

Host worker (Windows `agent.exe`):

```powershell
docker compose up -d postgres api
alembic upgrade head
.\.venv\Scripts\activate
python -m app.jobs.worker
```

Without Slack:

```powershell
curl -X POST http://127.0.0.1:8000/jobs -H "Content-Type: application/json" -d "{\"repository\":\"myapp\",\"request\":\"add a README comment\"}"
```

Then `GET /jobs/DEV-1` and `/jobs/DEV-1/events`.

Server: reverse-proxy the API, set Slack to `https://your.domain/slack/events`, `AGENT_WORKSPACE=/srv/ai-dev`.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Slack URL will not verify | API down, tunnel down, or signing secret mismatch |
| `401` on `/slack/events` | Signing secret or timestamp skew |
| Worker idle after Slack | Worker not running, or different `DATABASE_URL` |
| Clone / push / issues fail | Token scopes (need **Issues** write), org, or `ALLOWED_REPOS` |
| Worktree fails | `DEFAULT_GITHUB_BRANCH` wrong |
| `agent` not found | `CURSOR_CLI_BIN` / PATH |
| Job `FAILED`, no file changes | Agent did not edit files; see `/jobs/DEV-1/events` |
| Slack posts nothing | Missing `chat:write`, bot not in channel, empty token |

Architecture and command semantics: [README.md](README.md).
