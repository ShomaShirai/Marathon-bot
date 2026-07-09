from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models.channel_subscription import ChannelSubscription


class ChannelSubscriptionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(
        self,
        *,
        slack_team_id: str,
        slack_channel_id: str,
        category: str,
        registered_by: str,
    ) -> tuple[ChannelSubscription, bool]:
        existing = self.get_by_channel(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            category=category,
        )
        if existing is not None:
            return existing, False

        subscription = ChannelSubscription(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            category=category,
            registered_by=registered_by,
        )
        try:
            self.db.add(subscription)
            self.db.commit()
            self.db.refresh(subscription)
            return subscription, True
        except IntegrityError:
            self.db.rollback()
            existing = self.get_by_channel(
                slack_team_id=slack_team_id,
                slack_channel_id=slack_channel_id,
                category=category,
            )
            if existing is not None:
                return existing, False
            raise
        except SQLAlchemyError:
            self.db.rollback()
            raise

    def get_by_channel(
        self,
        *,
        slack_team_id: str,
        slack_channel_id: str,
        category: str,
    ) -> ChannelSubscription | None:
        statement = (
            select(ChannelSubscription)
            .where(ChannelSubscription.slack_team_id == slack_team_id)
            .where(ChannelSubscription.slack_channel_id == slack_channel_id)
            .where(ChannelSubscription.category == category)
        )
        return self.db.scalar(statement)

    def list_by_category(self, *, category: str) -> list[ChannelSubscription]:
        statement = (
            select(ChannelSubscription)
            .where(ChannelSubscription.category == category)
            .order_by(ChannelSubscription.id.asc())
        )
        return list(self.db.scalars(statement))
