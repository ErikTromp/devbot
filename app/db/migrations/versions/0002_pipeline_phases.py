"""pipeline phases and github issue identity

Revision ID: 0002_pipeline_phases
Revises: 0001_initial
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pipeline_phases"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("pipeline_phase", sa.String(32), nullable=True))
    op.add_column("jobs", sa.Column("phase_results", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("github_issue_number", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("github_issue_url", sa.String(512), nullable=True))
    op.create_index("ix_jobs_github_issue_number", "jobs", ["github_issue_number"])
    op.create_unique_constraint("uq_jobs_repo_issue", "jobs", ["repository", "github_issue_number"])


def downgrade() -> None:
    op.drop_constraint("uq_jobs_repo_issue", "jobs", type_="unique")
    op.drop_index("ix_jobs_github_issue_number", table_name="jobs")
    op.drop_column("jobs", "github_issue_url")
    op.drop_column("jobs", "github_issue_number")
    op.drop_column("jobs", "phase_results")
    op.drop_column("jobs", "pipeline_phase")
