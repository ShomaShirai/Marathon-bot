from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from sqlalchemy.orm import Session

from backend.app.repositories.channel_subscription_repository import ChannelSubscriptionRepository
from backend.app.services.race_service import CATEGORY_TENNIS, RaceService
from backend.app.services.scraping_service import USER_AGENT

logger = logging.getLogger(__name__)

TENNIS_TOURNAMENT_SOURCE_URL = "https://www.tennisbear.net/tournament/prefecture/pref12"
TENNISBEAR_HOSTS = {"www.tennisbear.net", "tennisbear.net"}
NAVIGATION_TIMEOUT_MS = 15000
TOURNAMENT_DETAIL_PATH_PATTERN = re.compile(r"^/tournament/[^/]+")
NON_DETAIL_TOURNAMENT_PATH_PREFIXES = (
    "/tournament/prefecture",
    "/tournament/region",
    "/tournament/ranking",
)


@dataclass(frozen=True)
class TennisTournamentSyncSummary:
    synced_count: int
    created_count: int


class TennisTournamentSyncService:
    def __init__(self, db: Session) -> None:
        self.subscription_repository = ChannelSubscriptionRepository(db)
        self.race_service = RaceService(db)

    def sync(self) -> TennisTournamentSyncSummary:
        subscriptions = self.subscription_repository.list_by_category(category=CATEGORY_TENNIS)
        if not subscriptions:
            return TennisTournamentSyncSummary(synced_count=0, created_count=0)

        tournament_urls = self._fetch_tournament_urls()
        created_count = 0
        for subscription in subscriptions:
            for tournament_url in tournament_urls:
                existing = self.race_service.get_by_url_for_slack_channel(
                    url=tournament_url,
                    slack_team_id=subscription.slack_team_id,
                    slack_channel_id=subscription.slack_channel_id,
                    category=CATEGORY_TENNIS,
                )
                if existing is not None:
                    continue

                self.race_service.register_from_url(
                    url=tournament_url,
                    slack_team_id=subscription.slack_team_id,
                    slack_channel_id=subscription.slack_channel_id,
                    registered_by=subscription.registered_by,
                    category=CATEGORY_TENNIS,
                )
                created_count += 1

        return TennisTournamentSyncSummary(
            synced_count=len(tournament_urls),
            created_count=created_count,
        )

    def _fetch_tournament_urls(self) -> list[str]:
        try:
            return self._fetch_tournament_urls_with_playwright()
        except Exception as exc:
            logger.warning("tennis tournament sync failed source_url=%s error=%s", TENNIS_TOURNAMENT_SOURCE_URL, exc)
            return []

    def _fetch_tournament_urls_with_playwright(self) -> list[str]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
                page.goto(TENNIS_TOURNAMENT_SOURCE_URL, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT_MS)
                raw_hrefs = page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll("a[href]"))
                        .map((anchor) => anchor.getAttribute("href") || "")
                    """
                )
            finally:
                browser.close()

        return self._select_tournament_urls(raw_hrefs)

    def _select_tournament_urls(self, raw_hrefs: Any) -> list[str]:
        urls: list[str] = []
        seen_urls: set[str] = set()
        if not isinstance(raw_hrefs, list):
            return urls

        for raw_href in raw_hrefs:
            if not isinstance(raw_href, str):
                continue

            absolute_url = urljoin(TENNIS_TOURNAMENT_SOURCE_URL, raw_href)
            normalized_url = self._normalize_tournament_url(absolute_url)
            if normalized_url is None or normalized_url in seen_urls:
                continue

            seen_urls.add(normalized_url)
            urls.append(normalized_url)

        return urls

    def _normalize_tournament_url(self, url: str) -> str | None:
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"}:
            return None
        if parsed_url.hostname not in TENNISBEAR_HOSTS:
            return None
        if not self._is_tournament_detail_path(parsed_url.path):
            return None

        return urlunparse(
            (
                "https",
                "www.tennisbear.net",
                parsed_url.path.rstrip("/"),
                "",
                "",
                "",
            )
        )

    def _is_tournament_detail_path(self, path: str) -> bool:
        if not TOURNAMENT_DETAIL_PATH_PATTERN.match(path):
            return False
        return not any(path.startswith(prefix) for prefix in NON_DETAIL_TOURNAMENT_PATH_PREFIXES)
