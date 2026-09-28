from __future__ import annotations

from pathlib import Path


def git_cmd(*args: str, cwd: Path | None = None) -> list[str]:
    del cwd
    return ["git", *args]


def fetch_args() -> list[str]:
    return ["git", "fetch", "origin"]


def worktree_add_args(path: Path, branch: str, start_point: str) -> list[str]:
    return ["git", "worktree", "add", str(path), "-b", branch, start_point]


def worktree_add_existing_args(path: Path, branch: str) -> list[str]:
    return ["git", "worktree", "add", str(path), branch]


def worktree_remove_args(path: Path) -> list[str]:
    return ["git", "worktree", "remove", "--force", str(path)]


def branch_exists_args(branch: str) -> list[str]:
    return ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]


def commit_args(message: str) -> list[str]:
    return ["git", "commit", "-m", message]


def add_all_args() -> list[str]:
    return ["git", "add", "-A"]


def head_sha_args() -> list[str]:
    return ["git", "rev-parse", "HEAD"]


def push_args(branch: str) -> list[str]:
    return ["git", "push", "-u", "origin", branch]


def delete_branch_args(branch: str) -> list[str]:
    return ["git", "branch", "-D", branch]


def status_args() -> list[str]:
    return ["git", "status", "--porcelain"]


def diff_args(*, staged: bool = False) -> list[str]:
    return ["git", "diff", "--cached", "--stat"] if staged else ["git", "diff", "--stat"]


def rebase_args(ref: str) -> list[str]:
    return ["git", "rebase", ref]


def merge_args(ref: str) -> list[str]:
    return ["git", "merge", "--no-ff", ref]


def clone_args(url: str, dest: Path) -> list[str]:
    return ["git", "clone", "--", url, str(dest)]


def auth_header_args(token: str) -> list[str]:
    """Rewrite github.com HTTPS URLs through a tokenized host for this process only."""
    return [
        "-c",
        f"url.https://x-access-token:{token}@github.com/.insteadOf=https://github.com/",
    ]
