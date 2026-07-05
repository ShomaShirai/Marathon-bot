"""add last extraction method to races

Revision ID: 20260704_000003
Revises: 20260704_000002
Create Date: 2026-07-04 00:00:03.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260704_000003"
down_revision: Union[str, None] = "20260704_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "races",
        sa.Column("last_extraction_method", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("races", "last_extraction_method")
