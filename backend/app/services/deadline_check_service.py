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
ENTRY_START_NOTIFICATION_TYPES = {
    range(15, 31): "entry_start_30_days_before",
    range(8, 15): "entry_start_14_days_before",
    range(0, 8): "entry_start_7_days_before",
}
ENTRY_DEADLINE_NOTIFICATION_TYPES = {
    range(15, 31): "entry_deadline_30_days_before",
    range(8, 15): "entry_deadline_14_days_before",
    range(0, 8): "entry_deadline_7_days_before",
}


@dataclass(frozen=True)
class DeadlineCheckSummary:
    checked_count: int
    updated_count: int
    notified_count: int
    failed_count: int
    html_count: int
    llm_count: int


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
        races = self.race_repository.list_all()
        checked_count = 0
        updated_count = 0
        notified_count = 0
        failed_count = 0
        html_count = sum(1 for race in races if race.last_extraction_method == "html")
        llm_count = sum(1 for race in races if race.last_extraction_method == "llm")

        for race in races:
            checked_count += 1
            try:
                check_result = self.race_service.check_registered_race(race)
            except Exception as exc:
                failed_count += 1
                self.db.rollback()
                logger.warning("deadline check failed race_id=%s error=%s", race.id, exc)
                continue

            if check_result.schedule_changed:
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
            html_count=html_count,
            llm_count=llm_count,
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

        candidates.extend(
            self._build_date_notification_candidates(
                race=race,
                today=today,
            )
        )

        return candidates

    def _build_date_notification_candidates(
        self,
        *,
        race: Race,
        today: date,
    ) -> list[NotificationCandidate]:
        candidates: list[NotificationCandidate] = []

        if race.entry_start_at is not None:
            start_date = race.entry_start_at.date()
            days_until_start = (start_date - today).days
            notification_type = self._notification_type_for_days_until(
                days_until=days_until_start,
                notification_ranges=ENTRY_START_NOTIFICATION_TYPES,
            )
            if notification_type is not None:
                candidates.append(
                    NotificationCandidate(
                        notification_type=notification_type,
                        dedupe_key=start_date.isoformat(),
                    )
                )

        if race.entry_deadline is not None:
            deadline_date = race.entry_deadline.date()
            days_until_deadline = (deadline_date - today).days
            notification_type = self._notification_type_for_days_until(
                days_until=days_until_deadline,
                notification_ranges=ENTRY_DEADLINE_NOTIFICATION_TYPES,
            )
            if notification_type is not None:
                candidates.append(
                    NotificationCandidate(
                        notification_type=notification_type,
                        dedupe_key=deadline_date.isoformat(),
                    )
                )

        return candidates

    def _notification_type_for_days_until(
        self,
        *,
        days_until: int,
        notification_ranges: dict[range, str],
    ) -> str | None:
        for notification_range, notification_type in notification_ranges.items():
            if days_until in notification_range:
                return notification_type

        return None

    def _schedule_key(self, race: Race) -> str:
        start_text = race.entry_start_at.isoformat() if race.entry_start_at else "-"
        deadline_text = race.entry_deadline.isoformat() if race.entry_deadline else "-"
        return f"start={start_text};deadline={deadline_text}"
