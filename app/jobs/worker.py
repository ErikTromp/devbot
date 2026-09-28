from __future__ import annotations

import logging
import os
import socket
import time

from app.agents.architecture import ArchitectureAgent
from app.agents.coding import CodingAgent
from app.agents.documentation import DocumentationAgent
from app.agents.elaborate import ElaborateTestAgent
from app.agents.refinement import RefinementAgent
from app.agents.security import SecurityAgent
from app.agents.testing import TestAgent
from app.config import get_settings
from app.db.session import session_scope
from app.github.client import GitHubClient
from app.jobs.pipeline import PipelineError, WorkerDeps, _fail_or_retry, run_claimed_job
from app.jobs.service import claim_next_job
from app.logging import configure_logging
from app.slack.client import SlackClient
from app.users import CredentialUserError, settings_for_job

logger = logging.getLogger(__name__)


def worker_identity(configured: str) -> str:
    return configured or f"{socket.gethostname()}:{os.getpid()}"


def build_deps(settings=None) -> WorkerDeps:
    settings = settings or get_settings()
    return WorkerDeps(
        settings=settings,
        slack=SlackClient(settings),
        github=GitHubClient(settings),
        coding=CodingAgent(settings),
        testing=TestAgent(settings),
        refinement=RefinementAgent(settings),
        elaborate=ElaborateTestAgent(settings),
        security=SecurityAgent(settings),
        architecture=ArchitectureAgent(settings),
        documentation=DocumentationAgent(settings),
    )


def deps_for_job(base: WorkerDeps, credential_user: str | None) -> WorkerDeps:
    job_settings = settings_for_job(base.settings, credential_user)
    if job_settings is base.settings:
        return base
    resolved = build_deps(job_settings)
    resolved.slack = base.slack
    return resolved


def run_once(deps: WorkerDeps | None = None, worker_id: str | None = None) -> bool:
    deps = deps or build_deps()
    worker_id = worker_id or worker_identity(deps.settings.worker_id)
    with session_scope(deps.settings) as session:
        job = claim_next_job(session, worker_id, deps.settings)
        if job is None:
            return False
        try:
            job_deps = deps_for_job(deps, job.credential_user)
        except CredentialUserError as exc:
            _fail_or_retry(session, job, deps, PipelineError(str(exc), retryable=False))
            return True
        run_claimed_job(session, job, job_deps, worker_id)
        return True


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.agent_workspace.mkdir(parents=True, exist_ok=True)
    worker_id = worker_identity(settings.worker_id)
    logger.info("worker_started", extra={"worker_id": worker_id})
    deps = build_deps()
    while True:
        try:
            worked = run_once(deps, worker_id)
            if not worked:
                time.sleep(settings.worker_poll_seconds)
        except KeyboardInterrupt:
            logger.info("worker_stopped")
            return
        except Exception:
            logger.exception("worker_loop_error")
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
