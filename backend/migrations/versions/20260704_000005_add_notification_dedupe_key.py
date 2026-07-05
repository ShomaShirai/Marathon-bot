"""add notification dedupe key

Revision ID: 20260704_000005
Revises: 20260704_000004
Create Date: 2026-07-04 00:00:05.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260704_000005"
down_revision: Union[str, None] = "20260704_000004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(sa.Column("dedupe_key", sa.String(length=255), nullable=True))

    op.execute("UPDATE notifications SET dedupe_key = 'legacy' WHERE dedupe_key IS NULL")

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.alter_column("dedupe_key", existing_type=sa.String(length=255), nullable=False)
        batch_op.drop_constraint("uq_notifications_race_id_notification_type", type_="unique")
        batch_op.create_unique_constraint(
            "uq_notifications_race_id_notification_type_dedupe_key",
            ["race_id", "notification_type", "dedupe_key"],
        )

    op.create_index(op.f("ix_notifications_dedupe_key"), "notifications", ["dedupe_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_dedupe_key"), table_name="notifications")

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_constraint("uq_notifications_race_id_notification_type_dedupe_key", type_="unique")
        batch_op.create_unique_constraint(
            "uq_notifications_race_id_notification_type",
            ["race_id", "notification_type"],
        )
        batch_op.drop_column("dedupe_key")
