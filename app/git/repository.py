from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.git.commands import (
    add_all_args,
    auth_header_args,
    branch_exists_args,
    clone_args,
    commit_args,
    delete_branch_args,
    diff_args,
    fetch_args,
    git_cmd,
    head_sha_args,
    merge_args,
    push_args,
    rebase_args,
    status_args,
)
from app.security.secrets import redact_secrets, sanitized_env

logger = logging.getLogger(__name__)

# Local orchestrator artifacts — never commit these.
EXCLUDED_FROM_COMMITS = (".devbot/plan.md",)


class GitError(RuntimeError):
    def __init__(self, message: str, result: subprocess.CompletedProcess[str] | None = None) -> None:
        super().__init__(redact_secrets(message))
        self.result = result


@dataclass
class GitResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def repo_cache_path(settings: Settings, repository: str) -> Path:
    safe = repository.replace("/", "__")
    return settings.agent_workspace.resolve() / "repos" / safe


def _safe_git_args(args: list[str]) -> list[str]:
    safe: list[str] = []
    skip_next = False
    for item in args:
        if skip_next:
            skip_next = False
            safe.append("[REDACTED]")
            continue
        if item == "-c":
            safe.append(item)
            skip_next = True
            continue
        if "x-access-token:" in item or "@github.com" in item:
            safe.append("https://github.com/[REDACTED]")
            continue
        safe.append(item)
    return safe[:8]


def run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    token: str | None = None,
    timeout: int = 120,
    check: bool = True,
) -> GitResult:
    command = list(args)
    if token and command and command[0] == "git":
        command = ["git", *auth_header_args(token), *command[1:]]
    env = sanitized_env(os.environ)
    env.pop("GIT_ASKPASS", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    logger.info("git_exec", extra={"git_args": _safe_git_args(command), "cwd": str(cwd) if cwd else None})
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
        env=env,
    )
    result = GitResult(
        args=args,
        returncode=completed.returncode,
        stdout=redact_secrets(completed.stdout),
        stderr=redact_secrets(completed.stderr),
    )
    if check and not result.ok:
        raise GitError(f"git failed ({completed.returncode}): {result.stderr or result.stdout}", completed)
    return result


class GitRepository:
    def __init__(self, path: Path, settings: Settings) -> None:
        self.path = path
        self.settings = settings

    def fetch(self) -> GitResult:
        return run_git(fetch_args(), cwd=self.path, token=self.settings.github_token)

    def status(self) -> GitResult:
        return run_git(status_args(), cwd=self.path)

    def diff(self, *, staged: bool = False) -> GitResult:
        return run_git(diff_args(staged=staged), cwd=self.path)

    def branch_exists(self, branch: str) -> bool:
        result = run_git(branch_exists_args(branch), cwd=self.path, check=False)
        return result.ok

    def head_sha(self) -> str:
        return run_git(head_sha_args(), cwd=self.path).stdout.strip()

    def soft_reset(self, ref: str) -> GitResult:
        """Undo agent commits but keep their file changes staged for orchestrator commit."""
        return run_git(["git", "reset", "--soft", ref], cwd=self.path)

    def git_path(self, relative: str) -> Path:
        """Resolve a path inside the git dir. Worktrees store `.git` as a file, not a directory."""
        result = run_git(["git", "rev-parse", "--git-path", relative], cwd=self.path)
        raw = Path(result.stdout.strip())
        return raw if raw.is_absolute() else (self.path / raw).resolve()

    def ensure_devbot_plan_excluded(self) -> None:
        """Keep .devbot/plan.md local to the worktree; drop it from the index if tracked."""
        info = self.git_path("info/exclude")
        info.parent.mkdir(parents=True, exist_ok=True)
        block = "\n# devbot local plan (never commit)\n.devbot/plan.md\n"
        text = info.read_text(encoding="utf-8") if info.exists() else ""
        if ".devbot/plan.md" not in text:
            info.write_text(text.rstrip() + block, encoding="utf-8")
        run_git(["git", "rm", "--cached", "-f", ".devbot/plan.md"], cwd=self.path, check=False)

    def _porcelain_excluding(self, *exclude: str) -> str:
        lines = [
            line
            for line in self.status().stdout.splitlines()
            if line.strip() and not any(part in line for part in exclude)
        ]
        return "\n".join(lines)

    def has_committable_changes(self) -> bool:
        return bool(self._porcelain_excluding(*EXCLUDED_FROM_COMMITS).strip())

    def stage_for_commit(self) -> None:
        run_git(add_all_args(), cwd=self.path)
        for path in EXCLUDED_FROM_COMMITS:
            run_git(["git", "reset", "HEAD", "--", path], cwd=self.path, check=False)

    def has_staged_changes(self) -> bool:
        result = run_git(["git", "diff", "--cached", "--quiet"], cwd=self.path, check=False)
        return not result.ok

    def commit(self, message: str) -> GitResult:
        self.stage_for_commit()
        if not self.has_staged_changes():
            raise GitError("nothing to commit after excluding local devbot artifacts")
        return run_git(commit_args(message), cwd=self.path)

    def ensure_head_branch(self, branch: str) -> None:
        """Stay on the ticket branch. Never move an existing branch pointer onto another HEAD."""
        head = run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.path).stdout.strip()
        if head == branch:
            return
        if self.branch_exists(branch):
            raise GitError(
                f"Worktree is on `{head}` but `{branch}` already exists. Refusing to recreate that branch from `{head}`."
            )
        run_git(["git", "checkout", "-b", branch], cwd=self.path)

    def contains_commit(self, sha: str) -> bool:
        result = run_git(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=self.path, check=False)
        return result.ok

    def remote_head_sha(self, branch: str) -> str:
        result = run_git(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=self.path,
            token=self.settings.github_token,
            timeout=60,
        )
        line = next((item for item in result.stdout.splitlines() if item.strip()), "")
        sha = line.split()[0] if line else ""
        if not sha:
            raise GitError(f"origin/{branch} is missing after push")
        return sha

    def push(self, branch: str) -> GitResult:
        result = run_git(push_args(branch), cwd=self.path, token=self.settings.github_token, timeout=180)
        local = self.head_sha()
        remote = self.remote_head_sha(branch)
        if remote != local:
            raise GitError(f"origin/{branch} is {remote}, local HEAD is {local}")
        return result

    def delete_local_branch(self, branch: str) -> GitResult:
        return run_git(delete_branch_args(branch), cwd=self.path, check=False)

    def rebase(self, ref: str) -> GitResult:
        result = run_git(rebase_args(ref), cwd=self.path, check=False)
        if not result.ok and _is_conflict(result):
            raise GitError(f"rebase conflict: {result.stderr}", None)
        if not result.ok:
            raise GitError(result.stderr or "rebase failed")
        return result

    def merge(self, ref: str) -> GitResult:
        result = run_git(merge_args(ref), cwd=self.path, check=False)
        if not result.ok and _is_conflict(result):
            raise GitError(f"merge conflict: {result.stderr}")
        if not result.ok:
            raise GitError(result.stderr or "merge failed")
        return result

    def has_changes(self) -> bool:
        return self.has_committable_changes()

    def configure_identity(self) -> None:
        run_git(git_cmd("config", "user.email", "devbot@local"), cwd=self.path, check=False)
        run_git(git_cmd("config", "user.name", "devbot"), cwd=self.path, check=False)


def ensure_repo_cache(settings: Settings, repository: str) -> GitRepository:
    dest = repo_cache_path(settings, repository)
    dest.parent.mkdir(parents=True, exist_ok=True)
    token = settings.github_token or None
    if not (dest / ".git").exists():
        url = f"https://github.com/{repository}.git"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and not any(dest.iterdir()):
            dest.rmdir()
        run_git(clone_args(url, dest), token=token, timeout=300)
    repo = GitRepository(dest, settings)
    repo.fetch()
    return repo


def _is_conflict(result: GitResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    return "conflict" in text or "could not apply" in text
