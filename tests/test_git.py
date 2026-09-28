from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.git.commands import auth_header_args, branch_exists_args, clone_args, commit_args, push_args, worktree_add_args
from app.git.repository import GitError, GitRepository, run_git
from app.git.worktree import create_worktree, worktree_path_for
from app.jobs.models import branch_name_for


def test_command_construction() -> None:
    assert worktree_add_args(Path("/srv/ai-dev/jobs/DEV-42/worktree"), "agent/DEV-42", "origin/main") == [
        "git",
        "worktree",
        "add",
        str(Path("/srv/ai-dev/jobs/DEV-42/worktree")),
        "-b",
        "agent/DEV-42",
        "origin/main",
    ]
    assert branch_name_for(42) == "agent/DEV-42"
    assert commit_args("msg") == ["git", "commit", "-m", "msg"]
    assert push_args("agent/DEV-42") == ["git", "push", "-u", "origin", "agent/DEV-42"]
    assert clone_args("https://github.com/acme/myapp.git", Path("/tmp/repo"))[0:3] == ["git", "clone", "--"]
    assert branch_exists_args("agent/DEV-1")[-1] == "refs/heads/agent/DEV-1"
    rewrite = auth_header_args("secret-token")
    assert rewrite[0] == "-c"
    assert "insteadOf=https://github.com/" in rewrite[1]
    assert "secret-token" in rewrite[1]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_push_requires_matching_remote_sha(tmp_path: Path, settings, monkeypatch) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo_path, check=True, capture_output=True)
    repo = GitRepository(repo_path, settings)
    repo.push("main")
    assert repo.remote_head_sha("main") == repo.head_sha()
    assert repo.contains_commit(repo.head_sha())
    assert not repo.contains_commit("b" * 40)
    monkeypatch.setattr(GitRepository, "remote_head_sha", lambda self, branch: "a" * 40)
    with pytest.raises(GitError, match="local HEAD"):
        repo.push("main")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_commit_excludes_devbot_plan(tmp_path: Path, settings) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True)
    repo = GitRepository(repo_path, settings)
    plan = repo_path / ".devbot" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# secret plan\n", encoding="utf-8")
    (repo_path / "feature.txt").write_text("work", encoding="utf-8")
    repo.ensure_devbot_plan_excluded()
    repo.commit("#1: implement")
    log = subprocess.run(["git", "log", "-1", "--name-only"], cwd=repo_path, capture_output=True, text=True, check=True)
    assert "feature.txt" in log.stdout
    assert ".devbot/plan.md" not in log.stdout
    assert plan.read_text(encoding="utf-8") == "# secret plan\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_worktree_creation(tmp_path: Path, settings) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=origin, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True, capture_output=True)
    (origin / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=origin, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=origin, check=True, capture_output=True)
    cache = tmp_path / "cache"
    subprocess.run(["git", "clone", str(origin), str(cache)], check=True, capture_output=True)
    run_git(["git", "remote", "rename", "origin", "origin"], cwd=cache, check=False)
    settings.agent_workspace = tmp_path / "ws"
    repo = GitRepository(cache, settings)
    run_git(["git", "branch", "-M", "main"], cwd=cache, check=False)
    # clone already has origin/main
    path, how = create_worktree(repo, settings, 42, "agent/DEV-42")
    assert how == "from_base"
    assert path == worktree_path_for(settings, 42)
    assert path.exists()
    assert (path / "README.md").read_text(encoding="utf-8") == "hi"
    result = subprocess.run(["git", "branch", "--show-current"], cwd=path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "agent/DEV-42"
    again, how_again = create_worktree(repo, settings, 42, "agent/DEV-42")
    assert again == path
    assert how_again == "reused"
    assert (path / ".git").is_file()
    GitRepository(path, settings).ensure_devbot_plan_excluded()
    exclude = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    exclude_path = Path(exclude) if Path(exclude).is_absolute() else (path / exclude).resolve()
    assert ".devbot/plan.md" in exclude_path.read_text(encoding="utf-8")
    with pytest.raises(GitError, match="Refusing to create"):
        create_worktree(repo, settings, 99, "agent/DEV-99", allow_new_branch=False)
