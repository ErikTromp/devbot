from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Job
from app.git.repository import GitRepository, repo_cache_path
from app.git.worktree import job_root, remove_worktree
from app.github.client import GitHubClient
from app.logging import log_extra

logger = logging.getLogger(__name__)


def delete_job(session: Session, job: Job) -> None:
    session.delete(job)
    session.flush()


def cleanup_local(settings: Settings, job: Job) -> list[str]:
    done: list[str] = []
    worktree = Path(job.worktree_path) if job.worktree_path else None
    cache_dir = repo_cache_path(settings, job.repository)
    if worktree and worktree.exists() and cache_dir.exists():
        try:
            remove_worktree(GitRepository(cache_dir, settings), worktree, keep_metadata=False)
            done.append("worktree")
        except Exception:
            logger.warning("remove_worktree_failed", extra=log_extra(job_id=job.id, repository=job.repository))
    if job.branch_name and cache_dir.exists():
        try:
            GitRepository(cache_dir, settings).delete_local_branch(job.branch_name)
            done.append(f"local branch `{job.branch_name}`")
        except Exception:
            logger.warning("delete_local_branch_failed", extra=log_extra(job_id=job.id))
    root = job_root(settings, job.id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
        done.append("job directory")
    return done


def cleanup_github(github: GitHubClient, job: Job) -> list[str]:
    done: list[str] = []
    if job.pull_request_number:
        try:
            github.close_pull_request(job.repository, job.pull_request_number)
            done.append(f"PR #{job.pull_request_number} closed")
        except Exception:
            logger.warning("close_pr_failed", extra=log_extra(job_id=job.id))
    if job.github_issue_number:
        try:
            github.close_issue(job.repository, job.github_issue_number)
            done.append(f"issue {job.issue_ref} closed")
        except Exception:
            logger.warning("close_issue_failed", extra=log_extra(job_id=job.id))
    if job.branch_name:
        try:
            github.delete_branch(job.repository, job.branch_name)
            done.append(f"remote `{job.branch_name}` deleted")
        except Exception:
            logger.warning("delete_remote_branch_failed", extra=log_extra(job_id=job.id))
    return done


def remove_job(session: Session, job: Job, settings: Settings, github: GitHubClient) -> list[str]:
    ref = job.issue_ref
    title = job.issue_title
    notes = cleanup_github(github, job)
    notes.extend(cleanup_local(settings, job))
    delete_job(session, job)
    notes.append("Postgres job deleted")
    session.commit()
    return [f"Removed {ref} — {title}", *notes]
