from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class Race(Base):
    __tablename__ = "races"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slack_team_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slack_channel_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        default="marathon",
        server_default=text("'marathon'"),
    )
    registered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    page_status: Mapped[str | None] = mapped_column(String(50))
    entry_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_status: Mapped[str | None] = mapped_column(String(50))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_content_hash: Mapped[str | None] = mapped_column(String(255))
    last_extraction_method: Mapped[str | None] = mapped_column(String(50))
    last_detected_text: Mapped[str | None] = mapped_column(Text)
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

    events: Mapped[list["RaceEvent"]] = relationship(
        "RaceEvent",
        back_populates="race",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="race",
        cascade="all, delete-orphan",
    )
