"""named credential user on jobs

Revision ID: 0003_credential_user
Revises: 0002_pipeline_phases
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_credential_user"
down_revision: Union[str, None] = "0002_pipeline_phases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("credential_user", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "credential_user")
