from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models.race import Race


class RaceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, race: Race) -> Race:
        try:
            self.db.add(race)
            self.db.commit()
            self.db.refresh(race)
            return race
        except SQLAlchemyError:
            self.db.rollback()
            raise
