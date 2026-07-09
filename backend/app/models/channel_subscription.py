from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ChannelSubscription(Base):
    __tablename__ = "channel_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "slack_team_id",
            "slack_channel_id",
            "category",
            name="uq_channel_subscriptions_team_channel_category",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slack_team_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slack_channel_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    registered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
