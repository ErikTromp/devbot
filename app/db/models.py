from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.jobs.models import JobStatus, job_display_id


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("repository", "github_issue_number", name="uq_jobs_repo_issue"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    request: Mapped[str] = mapped_column(Text)
    slack_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slack_thread_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED.value, index=True)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phase_results: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(SQLiteJSON(), "sqlite"), nullable=True)
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_issue_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pull_request_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pull_request_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    specification: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(SQLiteJSON(), "sqlite"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[JobEvent]] = relationship(back_populates="job", cascade="all, delete-orphan")

    @property
    def display_id(self) -> str:
        return job_display_id(self.id)

    @property
    def issue_ref(self) -> str:
        return f"#{self.github_issue_number}" if self.github_issue_number else self.display_id

    @property
    def issue_title(self) -> str:
        spec = self.specification or {}
        title = str(spec.get("title") or "").strip()
        if title:
            return title[:80]
        return self.request.strip().split("\n", 1)[0][:80]

    @property
    def status_enum(self) -> JobStatus:
        return JobStatus(self.status)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(SQLiteJSON(), "sqlite"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[Job] = relationship(back_populates="events")


class IncomingWebhookEvent(Base):
    __tablename__ = "incoming_webhook_events"
    __table_args__ = (UniqueConstraint("source", "delivery_id", name="uq_webhook_source_delivery"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    delivery_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(SQLiteJSON(), "sqlite"), nullable=True)
    processed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
