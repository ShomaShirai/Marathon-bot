from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.app.core.config import is_local_env
from backend.app.repositories.channel_subscription_repository import ChannelSubscriptionRepository
from backend.app.services.race_service import CATEGORY_TENNIS, RaceService
from backend.app.services.scraping_service import ScrapingService
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
JST = ZoneInfo("Asia/Tokyo")
TARGET_LEVEL_KEYWORDS = ("初中級", "初級", "初心者", "初級者", "初中級者")
FULL_TOURNAMENT_KEYWORDS = ("満員", "キャンセル待ち", "募集終了")
TOURNAMENT_LOOKAHEAD_DAYS = 90
MAX_DETAIL_ENRICHMENT_COUNT = 20
LOCAL_LOG_TEXT_LIMIT = 500
DATE_PATTERNS = (
    re.compile(r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"),
    re.compile(r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"),
    re.compile(r"(?<!\d)(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"),
    re.compile(r"(?<!\d)(?P<month>\d{1,2})[/-](?P<day>\d{1,2})(?!\d)"),
)


@dataclass(frozen=True)
class TennisTournamentSyncSummary:
    synced_count: int
    created_count: int
    skipped_full_count: int = 0
    skipped_date_count: int = 0
    skipped_level_count: int = 0
    skipped_unknown_count: int = 0


@dataclass(frozen=True)
class TennisTournamentCandidate:
    url: str
    title: str | None
    event_date: date | None
    status_text: str | None
    raw_text: str


@dataclass(frozen=True)
class TennisTournamentFilterResult:
    candidates: list[TennisTournamentCandidate]
    skipped_full_count: int
    skipped_date_count: int
    skipped_level_count: int
    skipped_unknown_count: int


class TennisTournamentSyncService:
    def __init__(self, db: Session) -> None:
        self.subscription_repository = ChannelSubscriptionRepository(db)
        self.race_service = RaceService(db)
        self.scraping_service = ScrapingService()

    def sync(self) -> TennisTournamentSyncSummary:
        subscriptions = self.subscription_repository.list_by_category(category=CATEGORY_TENNIS)
        self._log_local("subscription_count=%s", len(subscriptions))
        if not subscriptions:
            self._log_local("skip scraping because no tennis channel subscription exists")
            return TennisTournamentSyncSummary(synced_count=0, created_count=0)

        filter_result = self._fetch_filtered_tournament_candidates()
        created_count = 0
        for subscription in subscriptions:
            for candidate in filter_result.candidates:
                existing = self.race_service.get_by_url_for_slack_channel(
                    url=candidate.url,
                    slack_team_id=subscription.slack_team_id,
                    slack_channel_id=subscription.slack_channel_id,
                    category=CATEGORY_TENNIS,
                )
                if existing is not None:
                    continue

                self.race_service.register_from_url(
                    url=candidate.url,
                    slack_team_id=subscription.slack_team_id,
                    slack_channel_id=subscription.slack_channel_id,
                    registered_by=subscription.registered_by,
                    category=CATEGORY_TENNIS,
                )
                created_count += 1

        return TennisTournamentSyncSummary(
            synced_count=len(filter_result.candidates),
            created_count=created_count,
            skipped_full_count=filter_result.skipped_full_count,
            skipped_date_count=filter_result.skipped_date_count,
            skipped_level_count=filter_result.skipped_level_count,
            skipped_unknown_count=filter_result.skipped_unknown_count,
        )

    def _fetch_filtered_tournament_candidates(self) -> TennisTournamentFilterResult:
        try:
            candidates = self._fetch_tournament_candidates_with_playwright()
        except Exception as exc:
            logger.warning("tennis tournament sync failed source_url=%s error=%s", TENNIS_TOURNAMENT_SOURCE_URL, exc)
            return TennisTournamentFilterResult(
                candidates=[],
                skipped_full_count=0,
                skipped_date_count=0,
                skipped_level_count=0,
                skipped_unknown_count=1,
            )

        self._log_local("scraped_candidate_count=%s", len(candidates))
        self._log_local_candidates("scraped", candidates)
        return self._filter_tournament_candidates(candidates)

    def _fetch_tournament_candidates_with_playwright(self) -> list[TennisTournamentCandidate]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
                page.goto(TENNIS_TOURNAMENT_SOURCE_URL, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT_MS)
                raw_candidates = page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll("a[href]"))
                        .map((anchor) => {
                            const containers = [
                                anchor.closest("article"),
                                anchor.closest("li"),
                                anchor.closest("[class*='card']"),
                                anchor.closest("[class*='Card']"),
                                anchor.closest("[class*='tournament']"),
                                anchor.closest("[class*='Tournament']"),
                                anchor.parentElement,
                            ].filter(Boolean);
                            const container = containers
                                .sort((a, b) => (a.innerText || "").length - (b.innerText || "").length)[0] || anchor;
                            return {
                                href: anchor.getAttribute("href") || "",
                                title: (anchor.innerText || anchor.getAttribute("aria-label") || "").trim(),
                                text: (container.innerText || anchor.innerText || "").trim(),
                            };
                        })
                    """
                )
            finally:
                browser.close()

        return self._select_tournament_candidates(raw_candidates)

    def _select_tournament_candidates(self, raw_candidates: Any) -> list[TennisTournamentCandidate]:
        candidates: list[TennisTournamentCandidate] = []
        seen_urls: set[str] = set()
        if not isinstance(raw_candidates, list):
            return candidates

        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                continue

            raw_href = str(raw_candidate.get("href") or "")
            absolute_url = urljoin(TENNIS_TOURNAMENT_SOURCE_URL, raw_href)
            normalized_url = self._normalize_tournament_url(absolute_url)
            if normalized_url is None or normalized_url in seen_urls:
                continue

            raw_text_value = str(raw_candidate.get("text") or "")
            raw_text = self._normalize_text(raw_text_value)
            anchor_title = self._normalize_text(str(raw_candidate.get("title") or ""))
            text_title = self._select_title_from_text(raw_text_value)
            title = self._select_candidate_title(anchor_title=anchor_title, text_title=text_title)
            seen_urls.add(normalized_url)
            candidates.append(
                TennisTournamentCandidate(
                    url=normalized_url,
                    title=title,
                    event_date=self._detect_event_date(raw_text),
                    status_text=self._detect_status_text(raw_text),
                    raw_text=raw_text,
                )
            )

        return candidates

    def _filter_tournament_candidates(
        self,
        candidates: list[TennisTournamentCandidate],
    ) -> TennisTournamentFilterResult:
        accepted_candidates: list[TennisTournamentCandidate] = []
        skipped_full_count = 0
        skipped_date_count = 0
        skipped_level_count = 0
        skipped_unknown_count = 0
        detail_enrichment_count = 0
        today = datetime.now(JST).date()
        latest_event_date = today + timedelta(days=TOURNAMENT_LOOKAHEAD_DAYS)

        for candidate in candidates:
            enriched_candidate = candidate
            if self._needs_detail_enrichment(candidate) and detail_enrichment_count < MAX_DETAIL_ENRICHMENT_COUNT:
                self._log_local_candidate("enriching", candidate)
                enriched_candidate = self._enrich_candidate_from_detail(candidate)
                self._log_local_candidate("enriched", enriched_candidate)
                detail_enrichment_count += 1

            if self._is_full_or_closed(enriched_candidate):
                skipped_full_count += 1
                self._log_local_candidate("skipped_full", enriched_candidate)
                continue

            if not self._has_target_level(enriched_candidate):
                skipped_level_count += 1
                self._log_local_candidate("skipped_level", enriched_candidate)
                continue

            if enriched_candidate.event_date is None:
                skipped_unknown_count += 1
                self._log_local_candidate("skipped_unknown_date", enriched_candidate)
                continue

            if not today <= enriched_candidate.event_date <= latest_event_date:
                skipped_date_count += 1
                self._log_local_candidate("skipped_out_of_range_date", enriched_candidate)
                continue

            accepted_candidates.append(enriched_candidate)
            self._log_local_candidate("accepted", enriched_candidate)

        self._log_local(
            "filter_summary accepted=%s skipped_full=%s skipped_date=%s skipped_level=%s skipped_unknown=%s",
            len(accepted_candidates),
            skipped_full_count,
            skipped_date_count,
            skipped_level_count,
            skipped_unknown_count,
        )
        return TennisTournamentFilterResult(
            candidates=accepted_candidates,
            skipped_full_count=skipped_full_count,
            skipped_date_count=skipped_date_count,
            skipped_level_count=skipped_level_count,
            skipped_unknown_count=skipped_unknown_count,
        )

    def _needs_detail_enrichment(self, candidate: TennisTournamentCandidate) -> bool:
        return candidate.event_date is None or candidate.title is None or not candidate.raw_text

    def _enrich_candidate_from_detail(self, candidate: TennisTournamentCandidate) -> TennisTournamentCandidate:
        try:
            metadata = self.scraping_service.fetch_metadata(candidate.url)
        except Exception as exc:
            logger.warning("tennis tournament detail enrichment failed url=%s error=%s", candidate.url, exc)
            return candidate

        detail_text = self._normalize_text(" ".join(part for part in (metadata.title, metadata.text) if part))
        raw_text = self._normalize_text(" ".join(part for part in (candidate.raw_text, detail_text) if part))
        return TennisTournamentCandidate(
            url=candidate.url,
            title=candidate.title or self._select_title_from_text(raw_text) or metadata.title,
            event_date=candidate.event_date or self._detect_event_date(raw_text),
            status_text=candidate.status_text or self._detect_status_text(raw_text),
            raw_text=raw_text,
        )

    def _is_full_or_closed(self, candidate: TennisTournamentCandidate) -> bool:
        text = self._candidate_text(candidate)
        return any(keyword in text for keyword in FULL_TOURNAMENT_KEYWORDS)

    def _has_target_level(self, candidate: TennisTournamentCandidate) -> bool:
        title = candidate.title or self._select_title_from_text(candidate.raw_text) or ""
        return self._contains_target_level(title)

    def _detect_status_text(self, text: str) -> str | None:
        for keyword in FULL_TOURNAMENT_KEYWORDS:
            if keyword in text:
                return keyword

        return None

    def _detect_event_date(self, text: str) -> date | None:
        now = datetime.now(JST).date()
        for pattern in DATE_PATTERNS:
            for match in pattern.finditer(text):
                event_date = self._build_event_date(match=match, today=now)
                if event_date is not None:
                    return event_date

        return None

    def _build_event_date(self, *, match: re.Match[str], today: date) -> date | None:
        try:
            year = int(match.groupdict().get("year") or today.year)
            month = int(match.group("month"))
            day = int(match.group("day"))
            event_date = date(year, month, day)
        except (ValueError, IndexError):
            return None

        if "year" not in match.groupdict() and event_date < today:
            try:
                event_date = date(today.year + 1, month, day)
            except ValueError:
                return None

        return event_date

    def _candidate_text(self, candidate: TennisTournamentCandidate) -> str:
        return " ".join(part for part in (candidate.title, candidate.status_text, candidate.raw_text) if part)

    def _select_candidate_title(
        self,
        *,
        anchor_title: str | None,
        text_title: str | None,
    ) -> str | None:
        if text_title and self._contains_target_level(text_title):
            return text_title
        if anchor_title:
            return anchor_title

        return text_title

    def _select_title_from_text(self, text: str) -> str | None:
        fallback_line: str | None = None
        for line in re.split(r"[\r\n]+", text):
            normalized_line = self._normalize_text(line)
            if not normalized_line:
                continue

            if fallback_line is None:
                fallback_line = normalized_line[:255]

            if self._contains_target_level(normalized_line):
                return normalized_line[:255]

        return fallback_line

    def _contains_target_level(self, text: str) -> bool:
        return any(keyword in text for keyword in TARGET_LEVEL_KEYWORDS)

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _log_local(self, message: str, *args: object) -> None:
        if not is_local_env():
            return

        logger.info("[tennis sync] " + message, *args)

    def _log_local_candidates(
        self,
        label: str,
        candidates: list[TennisTournamentCandidate],
    ) -> None:
        if not is_local_env():
            return

        for candidate in candidates:
            self._log_local_candidate(label, candidate)

    def _log_local_candidate(
        self,
        label: str,
        candidate: TennisTournamentCandidate,
    ) -> None:
        if not is_local_env():
            return

        logger.info(
            "[tennis sync] %s url=%s title=%r event_date=%s status=%r raw_text=%r",
            label,
            candidate.url,
            candidate.title,
            candidate.event_date.isoformat() if candidate.event_date else None,
            candidate.status_text,
            candidate.raw_text[:LOCAL_LOG_TEXT_LIMIT],
        )

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
