from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models.race import Race


class RaceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, race: Race) -> Race:
        try:
            self.add(race)
            self.commit()
            return race
        except SQLAlchemyError:
            self.rollback()
            raise

    def add(self, race: Race) -> Race:
        self.db.add(race)
        self.db.flush()
        return race

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def refresh(self, race: Race) -> None:
        self.db.refresh(race)
