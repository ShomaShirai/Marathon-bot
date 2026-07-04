"""add entry start at to races

Revision ID: 20260704_000002
Revises: 20260704_000001
Create Date: 2026-07-04 00:00:02.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260704_000002"
down_revision: Union[str, None] = "20260704_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "races",
        sa.Column("entry_start_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("races", "entry_start_at")
