from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import logging
import re
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
from sqlalchemy.orm import Session

from backend.app.core.config import is_local_env
from backend.app.models.race import Race
from backend.app.repositories.channel_subscription_repository import ChannelSubscriptionRepository
from backend.app.repositories.race_repository import RaceRepository
from backend.app.services.race_service import CATEGORY_TENNIS, RaceService
from backend.app.services.race_service import PAGE_STATUS_AVAILABLE

logger = logging.getLogger(__name__)

TENNIS_BEAR_BASE_URL = "https://www.tennisbear.net"
TENNIS_TOURNAMENT_SOURCE_URL = f"{TENNIS_BEAR_BASE_URL}/tournament/prefecture/pref12"
NAVIGATION_TIMEOUT_MS = 60000
JST = ZoneInfo("Asia/Tokyo")
TENNIS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TARGET_LEVEL_KEYWORDS = ("初中級", "初級", "初心者", "初級者", "初中級者")
FULL_TOURNAMENT_KEYWORDS = ("満員", "キャンセル待ち", "募集終了")
EXCLUDE_EVENT_KEYWORDS = (
    "Pickleball",
    "PICKLEBALL",
    "pickleball",
    "ピックルボール",
    "グリーンボール",
    "女子",
    "小学生",
    "中学生",
)
WAIT_KEYWORDS = TARGET_LEVEL_KEYWORDS + FULL_TOURNAMENT_KEYWORDS
LOCAL_LOG_TEXT_LIMIT = 500
EVENT_HREF_PATTERN = re.compile(r"^/event/\d+/info$")
EVENT_DATE_PATTERN = re.compile(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:\([^)]+\))?")
EVENT_TIME_PATTERN = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")


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
    entry_deadline: datetime | None
    status_text: str | None
    raw_text: str
    level: str | None = None
    location: str | None = None


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
        self.race_repository = RaceRepository(db)
        self.race_service = RaceService(db)

    @staticmethod
    def empty_summary() -> TennisTournamentSyncSummary:
        return TennisTournamentSyncSummary(synced_count=0, created_count=0)

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
                    self._update_existing_race_from_candidate(race=existing, candidate=candidate)
                    continue

                self._create_race_from_candidate(
                    candidate=candidate,
                    slack_team_id=subscription.slack_team_id,
                    slack_channel_id=subscription.slack_channel_id,
                    registered_by=subscription.registered_by,
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
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    viewport={"width": 1440, "height": 1200},
                    user_agent=TENNIS_USER_AGENT,
                )
                page = context.new_page()
                if is_local_env():
                    page.on("response", self._log_local_browser_response)
                page.set_default_timeout(NAVIGATION_TIMEOUT_MS)

                response = page.goto(
                    TENNIS_TOURNAMENT_SOURCE_URL,
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                self._log_local(
                    "document response status=%s final_url=%s",
                    response.status if response else "",
                    page.url,
                )
                self._wait_for_rendered_content(page=page, timeout_error=PlaywrightTimeoutError)
                self._scroll_to_load_more(page)
                html = page.content()
                self._diagnose_rendered_html(html)
                context.close()
            finally:
                browser.close()

        return self._extract_candidates_from_html(html)

    def _extract_candidates_from_html(self, html: str) -> list[TennisTournamentCandidate]:
        candidates: list[TennisTournamentCandidate] = []
        seen_urls: set[str] = set()
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.find_all("a", href=lambda href: href and EVENT_HREF_PATTERN.match(href))
        self._log_local("event_info_anchor_count=%s", len(anchors))

        for anchor in anchors:
            if not isinstance(anchor, Tag):
                continue

            candidate = self._parse_event_anchor(anchor)
            if candidate is None:
                continue
            if candidate.url in seen_urls:
                self._log_local_candidate("skipped_duplicate", candidate)
                continue

            seen_urls.add(candidate.url)
            candidates.append(candidate)

        return candidates

    def _parse_event_anchor(self, anchor: Tag) -> TennisTournamentCandidate | None:
        href = str(anchor.get("href") or "")
        if not EVENT_HREF_PATTERN.match(href):
            return None

        raw_text = self._normalize_text(anchor.get_text(" ", strip=True))
        columns = self._get_direct_column_texts(anchor)
        if len(columns) < 4:
            self._log_local("skip invalid_column_count href=%s text=%r", href, raw_text[:160])
            return None

        date_text = columns[1][0] if len(columns[1]) >= 1 else ""
        time_text = columns[1][1] if len(columns[1]) >= 2 else ""
        title, location = self._parse_title_and_location(columns[2])
        level = self._normalize_text(" ".join(columns[3]))
        event_datetime = self._parse_event_datetime(date_text=date_text, time_text=time_text)
        if event_datetime is None:
            self._log_local("skip datetime_parse_failed href=%s text=%r", href, raw_text[:160])
            return None

        return TennisTournamentCandidate(
            url=urljoin(TENNIS_BEAR_BASE_URL, href),
            title=title,
            event_date=event_datetime.date(),
            entry_deadline=event_datetime,
            status_text=self._detect_status_text(raw_text),
            raw_text=raw_text,
            level=level,
            location=location,
        )

    def _filter_tournament_candidates(
        self,
        candidates: list[TennisTournamentCandidate],
    ) -> TennisTournamentFilterResult:
        accepted_candidates: list[TennisTournamentCandidate] = []
        skipped_full_count = 0
        skipped_date_count = 0
        skipped_level_count = 0
        skipped_unknown_count = 0
        today = datetime.now(JST).date()
        latest_event_date = self._add_months(today, 3)

        for candidate in candidates:
            enriched_candidate = candidate

            if self._is_full_or_closed(enriched_candidate):
                skipped_full_count += 1
                self._log_local_candidate("skipped_full", enriched_candidate)
                continue

            if self._has_excluded_keyword(enriched_candidate):
                skipped_level_count += 1
                self._log_local_candidate("skipped_excluded_keyword", enriched_candidate)
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

    def _is_full_or_closed(self, candidate: TennisTournamentCandidate) -> bool:
        text = self._candidate_text(candidate)
        return any(keyword in text for keyword in FULL_TOURNAMENT_KEYWORDS)

    def _has_target_level(self, candidate: TennisTournamentCandidate) -> bool:
        text = self._candidate_text(candidate)
        return self._contains_target_level(text)

    def _has_excluded_keyword(self, candidate: TennisTournamentCandidate) -> bool:
        text = self._candidate_text(candidate)
        return any(keyword in text for keyword in EXCLUDE_EVENT_KEYWORDS)

    def _detect_status_text(self, text: str) -> str | None:
        for keyword in FULL_TOURNAMENT_KEYWORDS:
            if keyword in text:
                return keyword

        return None

    def _create_race_from_candidate(
        self,
        *,
        candidate: TennisTournamentCandidate,
        slack_team_id: str,
        slack_channel_id: str,
        registered_by: str,
    ) -> Race:
        checked_at = datetime.now(JST)
        race = Race(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            category=CATEGORY_TENNIS,
            registered_by=registered_by,
            title=(candidate.title or "テニス大会")[:255],
            url=candidate.url,
            source_domain="www.tennisbear.net",
            page_status=PAGE_STATUS_AVAILABLE,
            entry_start_at=None,
            entry_deadline=candidate.entry_deadline,
            entry_status=self._entry_status_for_candidate(candidate),
            last_checked_at=checked_at,
            last_content_hash=self._build_content_hash(candidate),
            last_extraction_method="playwright_html",
            last_detected_text=candidate.raw_text,
        )
        try:
            self.race_repository.add(race)
            self.race_repository.commit()
            self.race_repository.refresh(race)
            return race
        except Exception:
            self.race_repository.rollback()
            raise

    def _update_existing_race_from_candidate(
        self,
        *,
        race: Race,
        candidate: TennisTournamentCandidate,
    ) -> None:
        new_content_hash = self._build_content_hash(candidate)
        changed = False

        if candidate.title and race.title != candidate.title[:255]:
            race.title = candidate.title[:255]
            changed = True
        if candidate.entry_deadline is not None and race.entry_deadline != candidate.entry_deadline:
            race.entry_deadline = candidate.entry_deadline
            changed = True

        entry_status = self._entry_status_for_candidate(candidate)
        if race.entry_status != entry_status:
            race.entry_status = entry_status
            changed = True
        if race.last_content_hash != new_content_hash:
            race.last_content_hash = new_content_hash
            changed = True
        if race.last_detected_text != candidate.raw_text:
            race.last_detected_text = candidate.raw_text
            changed = True
        if race.last_extraction_method != "playwright_html":
            race.last_extraction_method = "playwright_html"
            changed = True
        if race.page_status != PAGE_STATUS_AVAILABLE:
            race.page_status = PAGE_STATUS_AVAILABLE
            changed = True

        race.last_checked_at = datetime.now(JST)
        if not changed:
            return

        try:
            self.race_repository.commit()
            self.race_repository.refresh(race)
        except Exception:
            self.race_repository.rollback()
            raise

    def _entry_status_for_candidate(self, candidate: TennisTournamentCandidate) -> str:
        if candidate.status_text:
            return "closed"

        return "open"

    def _build_content_hash(self, candidate: TennisTournamentCandidate) -> str:
        content = "|".join(
            [
                candidate.url,
                candidate.title or "",
                candidate.entry_deadline.isoformat() if candidate.entry_deadline else "",
                candidate.raw_text,
            ]
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _candidate_text(self, candidate: TennisTournamentCandidate) -> str:
        return " ".join(
            part
            for part in (
                candidate.title,
                candidate.status_text,
                candidate.level,
                candidate.location,
                candidate.raw_text,
            )
            if part
        )

    def _contains_target_level(self, text: str) -> bool:
        return any(keyword in text for keyword in TARGET_LEVEL_KEYWORDS)

    def _get_direct_column_texts(self, anchor: Tag) -> list[list[str]]:
        row = anchor.find("div", class_=lambda value: value and "row" in value.split())
        if not isinstance(row, Tag):
            return []

        columns: list[list[str]] = []
        for child in row.children:
            if not isinstance(child, Tag):
                continue

            texts = [self._normalize_text(text) for text in child.stripped_strings]
            columns.append([text for text in texts if text])

        return columns

    def _parse_title_and_location(self, detail_texts: list[str]) -> tuple[str, str]:
        for index, text in enumerate(detail_texts):
            if text.startswith("千葉県"):
                title = detail_texts[index - 1] if index > 0 else ""
                return title, text
        if len(detail_texts) >= 2:
            return detail_texts[-1], ""
        if len(detail_texts) == 1:
            return detail_texts[0], ""

        return "", ""

    def _parse_event_datetime(
        self,
        *,
        date_text: str,
        time_text: str,
        today: date | None = None,
    ) -> datetime | None:
        today = today or datetime.now(JST).date()
        date_match = EVENT_DATE_PATTERN.search(date_text)
        time_match = EVENT_TIME_PATTERN.search(time_text)
        if not date_match or not time_match:
            return None

        month = int(date_match.group("month"))
        day = int(date_match.group("day"))
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
        year = today.year

        try:
            event_date = date(year, month, day)
        except ValueError:
            return None

        if event_date < today:
            try:
                event_date = date(year + 1, month, day)
            except ValueError:
                return None

        return datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            hour,
            minute,
            tzinfo=JST,
        )

    def _add_months(self, value: date, months: int) -> date:
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        month_days = (
            31,
            29 if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        )
        return date(year, month, min(value.day, month_days[month - 1]))

    def _wait_for_rendered_content(self, *, page: Any, timeout_error: Any) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)
        except timeout_error:
            self._log_local("network idle wait timed out; continuing with current DOM")

        for keyword in WAIT_KEYWORDS:
            try:
                page.wait_for_selector(f"text={keyword}", timeout=5000)
                self._log_local("render keyword found keyword=%s", keyword)
                return
            except timeout_error:
                self._log_local("render keyword not found yet keyword=%s", keyword)

        self._log_local("no target keyword appeared before timeout; continuing for diagnostics")

    def _scroll_to_load_more(self, page: Any, rounds: int = 8) -> None:
        previous_height = 0
        stable_rounds = 0

        for index in range(rounds):
            height = page.evaluate("document.body.scrollHeight")
            self._log_local("scroll round=%s height=%s", index + 1, height)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)

            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == previous_height:
                stable_rounds += 1
            else:
                stable_rounds = 0

            previous_height = new_height
            if stable_rounds >= 2:
                self._log_local("scroll stopped because page height is stable")
                break

    def _diagnose_rendered_html(self, html: str) -> None:
        if not is_local_env():
            return

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n")
        self._log_local("rendered html length=%s text_length=%s", len(html), len(text))
        for keyword in WAIT_KEYWORDS:
            self._log_local("rendered keyword count keyword=%s count=%s", keyword, text.count(keyword))

        anchors = soup.find_all("a", href=lambda href: href and EVENT_HREF_PATTERN.match(href))
        self._log_local("rendered event info anchors=%s", len(anchors))
        for anchor in anchors[:30]:
            self._log_local(
                "rendered event link=%s text=%s",
                anchor["href"],
                self._normalize_text(anchor.get_text(" ", strip=True))[:160],
            )

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
            "[tennis sync] %s url=%s title=%r event_date=%s status=%r level=%r location=%r raw_text=%r",
            label,
            candidate.url,
            candidate.title,
            candidate.event_date.isoformat() if candidate.event_date else None,
            candidate.status_text,
            candidate.level,
            candidate.location,
            candidate.raw_text[:LOCAL_LOG_TEXT_LIMIT],
        )

    def _log_local_browser_response(self, response: Any) -> None:
        if not is_local_env():
            return
        if "tennisbear.net" not in response.url:
            return

        resource_type = response.request.resource_type
        if resource_type not in {"document", "xhr", "fetch"}:
            return

        logger.info(
            "[tennis sync] browser response status=%s type=%s url=%s",
            response.status,
            resource_type,
            response.url,
        )
