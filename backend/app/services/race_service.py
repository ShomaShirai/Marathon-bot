from datetime import UTC, datetime
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session

from backend.app.models.race import Race
from backend.app.repositories.race_event_repository import RaceEventRepository
from backend.app.repositories.race_repository import RaceRepository
from backend.app.services.deadline_detection_service import DeadlineDetectionService
from backend.app.services.deadline_detection_service import DeadlineDetectionResult
from backend.app.services.scraping_service import PageMetadata
from backend.app.services.scraping_service import ScrapingService


class InvalidRaceUrlError(ValueError):
    pass


class InvalidRaceIdError(ValueError):
    pass


class RaceService:
    def __init__(self, db: Session) -> None:
        self.repository = RaceRepository(db)
        self.event_repository = RaceEventRepository(db)
        self.scraping_service = ScrapingService()
        self.deadline_detection_service = DeadlineDetectionService()

    def register_from_url(
        self,
        *,
        url: str,
        slack_team_id: str,
        slack_channel_id: str,
        registered_by: str,
    ) -> Race:
        normalized_url, source_domain = self._normalize_url(url)
        checked_at = datetime.now(UTC)
        metadata, fetch_error = self._fetch_metadata(normalized_url)
        title = self._select_title(metadata, fallback=source_domain)
        detection = self._detect_deadline(metadata)

        race = Race(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            registered_by=registered_by,
            title=title,
            url=normalized_url,
            source_domain=source_domain,
            entry_deadline=detection.entry_deadline,
            entry_status=detection.entry_status,
            last_checked_at=checked_at,
            last_detected_text=detection.detected_text,
        )

        try:
            self.repository.add(race)
            self._record_registration_event(
                race=race,
                detection=detection,
                fetch_error=fetch_error,
            )
            self.repository.commit()
            self.repository.refresh(race)
            return race
        except Exception:
            self.repository.rollback()
            raise

    def list_by_slack_channel(
        self,
        *,
        slack_team_id: str,
        slack_channel_id: str,
        limit: int = 20,
    ) -> list[Race]:
        return self.repository.list_by_slack_channel(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            limit=limit,
        )

    def remove_by_id_for_slack_channel(
        self,
        *,
        race_id_text: str,
        slack_team_id: str,
        slack_channel_id: str,
    ) -> bool:
        race_id = self._parse_race_id(race_id_text)
        deleted_count = self.repository.delete_by_id_for_slack_channel(
            race_id=race_id,
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
        )
        return deleted_count > 0

    def _normalize_url(self, url: str) -> tuple[str, str]:
        normalized_url = self._strip_slack_url_markup(url.strip())
        parsed_url = urlparse(normalized_url)

        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise InvalidRaceUrlError("URLは http:// または https:// で始まる必要があります。")

        source_domain = parsed_url.hostname
        if not source_domain:
            raise InvalidRaceUrlError("URLのドメインを取得できませんでした。")

        return normalized_url, source_domain.lower()

    def _parse_race_id(self, race_id_text: str) -> int:
        try:
            race_id = int(race_id_text.strip())
        except ValueError as exc:
            raise InvalidRaceIdError("race_id は数値で指定してください。") from exc

        if race_id <= 0:
            raise InvalidRaceIdError("race_id は1以上の数値で指定してください。")

        return race_id

    def _strip_slack_url_markup(self, url: str) -> str:
        if not url.startswith("<") or not url.endswith(">"):
            return url

        inner_url = url[1:-1]
        return inner_url.split("|", maxsplit=1)[0]

    def _fetch_metadata(self, url: str) -> tuple[PageMetadata | None, str | None]:
        try:
            return self.scraping_service.fetch_metadata(url), None
        except requests.RequestException as exc:
            return None, str(exc)

    def _select_title(self, metadata: PageMetadata | None, *, fallback: str) -> str:
        if metadata is None:
            return fallback
        if not metadata.title:
            return fallback

        return metadata.title[:255]

    def _detect_deadline(self, metadata: PageMetadata | None) -> DeadlineDetectionResult:
        if metadata is None:
            return DeadlineDetectionResult(
                entry_deadline=None,
                entry_status="unknown",
                detected_text=None,
            )

        return self.deadline_detection_service.detect(metadata.text)

    def _record_registration_event(
        self,
        *,
        race: Race,
        detection: DeadlineDetectionResult,
        fetch_error: str | None,
    ) -> None:
        if fetch_error:
            self.event_repository.add(
                race_id=race.id,
                event_type="check_failed",
                new_value=fetch_error,
            )
            return

        if detection.entry_deadline:
            self.event_repository.add(
                race_id=race.id,
                event_type="deadline_detected",
                new_value=detection.entry_deadline.isoformat(),
            )
            return

        if detection.entry_status == "closed":
            self.event_repository.add(
                race_id=race.id,
                event_type="entry_closed",
                new_value=detection.detected_text,
            )
