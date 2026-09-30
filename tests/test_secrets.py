from app.security.secrets import sanitized_env


def test_subprocess_env_drops_worker_secrets_and_keeps_the_coding_key() -> None:
    env = sanitized_env(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/devbot",
            "GITHUB_TOKEN": "ghp_worker",
            "SLACK_BOT_TOKEN": "xoxb-worker",
            "SLACK_SIGNING_SECRET": "sign",
            "DATABASE_URL": "postgresql+psycopg://devbot:devbot@postgres/devbot",
            "DASHBOARD_PASSWORD": "s3cret",
            "POSTGRES_PASSWORD": "devbot",
            "CURSOR_API_KEY": "cursor_from_process",
            "COPILOT_GITHUB_TOKEN": "github_pat_from_process",
        },
        {"CURSOR_API_KEY": "cursor_for_this_job", "COPILOT_ALLOW_ALL": "true", "GITHUB_TOKEN": "ghp_smuggled"},
    )
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/devbot"
    assert env["CURSOR_API_KEY"] == "cursor_for_this_job"
    assert env["COPILOT_ALLOW_ALL"] == "true"
    assert "GITHUB_TOKEN" not in env
    assert "SLACK_BOT_TOKEN" not in env
    assert "SLACK_SIGNING_SECRET" not in env
    assert "DATABASE_URL" not in env
    assert "DASHBOARD_PASSWORD" not in env
    assert "POSTGRES_PASSWORD" not in env
    assert "COPILOT_GITHUB_TOKEN" not in env
