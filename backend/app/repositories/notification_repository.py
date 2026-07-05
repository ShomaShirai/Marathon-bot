from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.notification import Notification


class NotificationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def exists(
        self,
        *,
        race_id: int,
        notification_type: str,
        dedupe_key: str,
    ) -> bool:
        statement = (
            select(Notification.id)
            .where(Notification.race_id == race_id)
            .where(Notification.notification_type == notification_type)
            .where(Notification.dedupe_key == dedupe_key)
            .limit(1)
        )
        return self.db.scalar(statement) is not None

    def add(
        self,
        *,
        race_id: int,
        notification_type: str,
        dedupe_key: str,
    ) -> Notification | None:
        notification = Notification(
            race_id=race_id,
            notification_type=notification_type,
            dedupe_key=dedupe_key,
        )
        self.db.add(notification)
        self.db.flush()
        return notification
