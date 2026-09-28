from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.config import Settings
from app.git.commands import worktree_add_args, worktree_add_existing_args, worktree_remove_args
from app.git.repository import GitError, GitRepository, run_git
from app.jobs.models import job_display_id

logger = logging.getLogger(__name__)


def job_root(settings: Settings, job_id: int) -> Path:
    return settings.agent_workspace.resolve() / "jobs" / job_display_id(job_id)


def worktree_path_for(settings: Settings, job_id: int) -> Path:
    return job_root(settings, job_id) / "worktree"


def start_point(settings: Settings) -> str:
    return f"origin/{settings.default_github_branch}"


def create_worktree(
    cache: GitRepository,
    settings: Settings,
    job_id: int,
    branch: str,
    *,
    allow_new_branch: bool = True,
) -> tuple[Path, str]:
    """Return (path, how) where how is reused | reattached | from_remote | from_base."""
    path = worktree_path_for(settings, job_id)
    if path.exists() and (path / ".git").exists():
        logger.info("worktree_reused", extra={"job_id": job_id, "path": str(path)})
        return path, "reused"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.rmtree(path)
    cache.fetch()
    if cache.branch_exists(branch):
        run_git(worktree_add_existing_args(path, branch), cwd=cache.path)
        return path, "reattached"
    remote_ref = f"origin/{branch}"
    remote = run_git(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{remote_ref}"], cwd=cache.path, check=False)
    if remote.ok:
        run_git(["git", "worktree", "add", str(path), "-b", branch, remote_ref], cwd=cache.path)
        return path, "from_remote"
    if not allow_new_branch:
        raise GitError(
            f"Refusing to create `{branch}` from {start_point(settings)}. "
            f"`origin/{branch}` is missing, so a new worktree would drop commits that were not pushed."
        )
    run_git(worktree_add_args(path, branch, start_point(settings)), cwd=cache.path)
    return path, "from_base"


def remove_worktree(cache: GitRepository, path: Path, *, keep_metadata: bool = True) -> None:
    if not path.exists():
        return
    run_git(worktree_remove_args(path), cwd=cache.path, check=False)
    if path.exists() and not keep_metadata:
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() and keep_metadata:
        marker = path.parent / "worktree.removed"
        marker.write_text(str(path), encoding="utf-8")
        shutil.rmtree(path, ignore_errors=True)
