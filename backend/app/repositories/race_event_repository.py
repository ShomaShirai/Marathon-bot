from sqlalchemy.orm import Session

from backend.app.models.race_event import RaceEvent


class RaceEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(
        self,
        *,
        race_id: int,
        event_type: str,
        old_value: str | None = None,
        new_value: str | None = None,
    ) -> RaceEvent:
        race_event = RaceEvent(
            race_id=race_id,
            event_type=event_type,
            old_value=old_value,
            new_value=new_value,
        )
        self.db.add(race_event)
        self.db.flush()
        return race_event
