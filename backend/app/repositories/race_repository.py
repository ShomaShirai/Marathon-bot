from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import delete, select
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

    def list_by_slack_channel(
        self,
        *,
        slack_team_id: str,
        slack_channel_id: str,
        limit: int = 20,
    ) -> list[Race]:
        statement = (
            select(Race)
            .where(Race.slack_team_id == slack_team_id)
            .where(Race.slack_channel_id == slack_channel_id)
            .order_by(Race.created_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(statement))

    def list_all(self) -> list[Race]:
        statement = select(Race).order_by(Race.id.asc())
        return list(self.db.scalars(statement))

    def delete_by_id_for_slack_channel(
        self,
        *,
        race_id: int,
        slack_team_id: str,
        slack_channel_id: str,
    ) -> int:
        try:
            statement = (
                delete(Race)
                .where(Race.id == race_id)
                .where(Race.slack_team_id == slack_team_id)
                .where(Race.slack_channel_id == slack_channel_id)
            )
            result = self.db.execute(statement)
            self.db.commit()
            return result.rowcount or 0
        except SQLAlchemyError:
            self.rollback()
            raise
