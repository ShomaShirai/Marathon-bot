"""add categories and channel subscriptions

Revision ID: 20260709_000006
Revises: 20260704_000005
Create Date: 2026-07-09 00:00:06.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260709_000006"
down_revision: Union[str, None] = "20260704_000005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("races") as batch_op:
        batch_op.add_column(
            sa.Column(
                "category",
                sa.String(length=50),
                server_default="marathon",
                nullable=True,
            )
        )

    op.execute("UPDATE races SET category = 'marathon' WHERE category IS NULL")

    with op.batch_alter_table("races") as batch_op:
        batch_op.alter_column(
            "category",
            existing_type=sa.String(length=50),
            server_default="marathon",
            nullable=False,
        )

    op.create_index(op.f("ix_races_category"), "races", ["category"], unique=False)

    op.create_table(
        "channel_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slack_team_id", sa.String(length=255), nullable=False),
        sa.Column("slack_channel_id", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("registered_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "slack_team_id",
            "slack_channel_id",
            "category",
            name="uq_channel_subscriptions_team_channel_category",
        ),
    )
    op.create_index(
        op.f("ix_channel_subscriptions_slack_team_id"),
        "channel_subscriptions",
        ["slack_team_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_subscriptions_slack_channel_id"),
        "channel_subscriptions",
        ["slack_channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_subscriptions_category"),
        "channel_subscriptions",
        ["category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_channel_subscriptions_category"), table_name="channel_subscriptions")
    op.drop_index(op.f("ix_channel_subscriptions_slack_channel_id"), table_name="channel_subscriptions")
    op.drop_index(op.f("ix_channel_subscriptions_slack_team_id"), table_name="channel_subscriptions")
    op.drop_table("channel_subscriptions")
    op.drop_index(op.f("ix_races_category"), table_name="races")
    with op.batch_alter_table("races") as batch_op:
        batch_op.drop_column("category")
