from dataclasses import dataclass
from datetime import date, datetime
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.app.models.race import Race
from backend.app.repositories.notification_repository import NotificationRepository
from backend.app.repositories.race_repository import RaceRepository
from backend.app.services.race_service import RaceService
from backend.app.services.slack_notification_service import SlackNotificationService

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
DEADLINE_NOTIFICATION_TYPES = {
    7: "7_days_before",
    3: "3_days_before",
    1: "1_day_before",
    0: "deadline_today",
}


@dataclass(frozen=True)
class DeadlineCheckSummary:
    checked_count: int
    updated_count: int
    notified_count: int
    failed_count: int


@dataclass(frozen=True)
class NotificationCandidate:
    notification_type: str
    dedupe_key: str


class DeadlineCheckService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.race_repository = RaceRepository(db)
        self.notification_repository = NotificationRepository(db)
        self.race_service = RaceService(db)
        self.slack_notification_service = SlackNotificationService()

    def check_all(self, *, today: date | None = None) -> DeadlineCheckSummary:
        current_date = today or datetime.now(JST).date()
        checked_count = 0
        updated_count = 0
        notified_count = 0
        failed_count = 0

        for race in self.race_repository.list_all():
            checked_count += 1
            try:
                check_result = self.race_service.check_registered_race(race)
            except Exception as exc:
                failed_count += 1
                self.db.rollback()
                logger.warning("deadline check failed race_id=%s error=%s", race.id, exc)
                continue

            if check_result.changed:
                updated_count += 1
            if check_result.failed:
                failed_count += 1

            for candidate in self._build_notification_candidates(
                race=check_result.race,
                schedule_event_types=check_result.notification_events,
                today=current_date,
            ):
                if self.notification_repository.exists(
                    race_id=check_result.race.id,
                    notification_type=candidate.notification_type,
                    dedupe_key=candidate.dedupe_key,
                ):
                    continue

                try:
                    self.slack_notification_service.send_race_notification(
                        race=check_result.race,
                        notification_type=candidate.notification_type,
                    )
                    self.notification_repository.add(
                        race_id=check_result.race.id,
                        notification_type=candidate.notification_type,
                        dedupe_key=candidate.dedupe_key,
                    )
                    self.db.commit()
                    notified_count += 1
                except Exception as exc:
                    failed_count += 1
                    self.db.rollback()
                    logger.warning(
                        "slack notification failed race_id=%s notification_type=%s error=%s",
                        check_result.race.id,
                        candidate.notification_type,
                        exc,
                    )

        return DeadlineCheckSummary(
            checked_count=checked_count,
            updated_count=updated_count,
            notified_count=notified_count,
            failed_count=failed_count,
        )

    def _build_notification_candidates(
        self,
        *,
        race: Race,
        schedule_event_types: tuple[str, ...],
        today: date,
    ) -> list[NotificationCandidate]:
        candidates = [
            NotificationCandidate(
                notification_type=event_type,
                dedupe_key=self._schedule_key(race),
            )
            for event_type in schedule_event_types
        ]

        deadline_candidate = self._build_deadline_notification_candidate(race=race, today=today)
        if deadline_candidate is not None:
            candidates.append(deadline_candidate)

        return candidates

    def _build_deadline_notification_candidate(
        self,
        *,
        race: Race,
        today: date,
    ) -> NotificationCandidate | None:
        if race.entry_deadline is None:
            return None

        deadline_date = race.entry_deadline.date()
        days_until_deadline = (deadline_date - today).days
        notification_type = DEADLINE_NOTIFICATION_TYPES.get(days_until_deadline)
        if notification_type is None:
            return None

        return NotificationCandidate(
            notification_type=notification_type,
            dedupe_key=deadline_date.isoformat(),
        )

    def _schedule_key(self, race: Race) -> str:
        start_text = race.entry_start_at.isoformat() if race.entry_start_at else "-"
        deadline_text = race.entry_deadline.isoformat() if race.entry_deadline else "-"
        return f"start={start_text};deadline={deadline_text}"
