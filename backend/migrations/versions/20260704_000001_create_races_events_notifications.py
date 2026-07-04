"""create races events notifications

Revision ID: 20260704_000001
Revises: eee789cb3a5e
Create Date: 2026-07-04 00:00:01.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260704_000001"
down_revision: Union[str, None] = "eee789cb3a5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "races",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slack_team_id", sa.String(length=255), nullable=False),
        sa.Column("slack_channel_id", sa.String(length=255), nullable=False),
        sa.Column("registered_by", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(length=255), nullable=False),
        sa.Column("entry_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_status", sa.String(length=50), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_content_hash", sa.String(length=255), nullable=True),
        sa.Column("last_detected_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_races_slack_team_id"), "races", ["slack_team_id"], unique=False)
    op.create_index(op.f("ix_races_slack_channel_id"), "races", ["slack_channel_id"], unique=False)
    op.create_index(op.f("ix_races_source_domain"), "races", ["source_domain"], unique=False)

    op.create_table(
        "race_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["race_id"], ["races.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_race_events_race_id"), "race_events", ["race_id"], unique=False)
    op.create_index(op.f("ix_race_events_event_type"), "race_events", ["event_type"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["race_id"], ["races.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "race_id",
            "notification_type",
            name="uq_notifications_race_id_notification_type",
        ),
    )
    op.create_index(op.f("ix_notifications_race_id"), "notifications", ["race_id"], unique=False)
    op.create_index(op.f("ix_notifications_notification_type"), "notifications", ["notification_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_notification_type"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_race_id"), table_name="notifications")
    op.drop_table("notifications")

    op.drop_index(op.f("ix_race_events_event_type"), table_name="race_events")
    op.drop_index(op.f("ix_race_events_race_id"), table_name="race_events")
    op.drop_table("race_events")

    op.drop_index(op.f("ix_races_source_domain"), table_name="races")
    op.drop_index(op.f("ix_races_slack_channel_id"), table_name="races")
    op.drop_index(op.f("ix_races_slack_team_id"), table_name="races")
    op.drop_table("races")
