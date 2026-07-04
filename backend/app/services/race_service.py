from datetime import UTC, datetime
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session

from backend.app.models.race import Race
from backend.app.repositories.race_repository import RaceRepository
from backend.app.services.scraping_service import ScrapingService


class InvalidRaceUrlError(ValueError):
    pass


class RaceService:
    def __init__(self, db: Session) -> None:
        self.repository = RaceRepository(db)
        self.scraping_service = ScrapingService()

    def register_from_url(
        self,
        *,
        url: str,
        slack_team_id: str,
        slack_channel_id: str,
        registered_by: str,
    ) -> Race:
        normalized_url, source_domain = self._normalize_url(url)
        title = self._fetch_title(normalized_url, fallback=source_domain)

        race = Race(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            registered_by=registered_by,
            title=title,
            url=normalized_url,
            source_domain=source_domain,
            last_checked_at=datetime.now(UTC),
        )

        return self.repository.create(race)

    def _normalize_url(self, url: str) -> tuple[str, str]:
        normalized_url = self._strip_slack_url_markup(url.strip())
        parsed_url = urlparse(normalized_url)

        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise InvalidRaceUrlError("URLは http:// または https:// で始まる必要があります。")

        source_domain = parsed_url.hostname
        if not source_domain:
            raise InvalidRaceUrlError("URLのドメインを取得できませんでした。")

        return normalized_url, source_domain.lower()

    def _strip_slack_url_markup(self, url: str) -> str:
        if not url.startswith("<") or not url.endswith(">"):
            return url

        inner_url = url[1:-1]
        return inner_url.split("|", maxsplit=1)[0]

    def _fetch_title(self, url: str, *, fallback: str) -> str:
        try:
            metadata = self.scraping_service.fetch_metadata(url)
        except requests.RequestException:
            return fallback

        if not metadata.title:
            return fallback

        return metadata.title[:255]
