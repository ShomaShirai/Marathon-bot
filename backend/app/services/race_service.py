from dataclasses import dataclass
from datetime import UTC, datetime, time
import logging
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.orm import Session

from backend.app.core.config import is_local_env
from backend.app.models.race import Race
from backend.app.repositories.race_event_repository import RaceEventRepository
from backend.app.repositories.race_repository import RaceRepository
from backend.app.services.deadline_detection_service import DeadlineDetectionService
from backend.app.services.deadline_detection_service import DeadlineDetectionResult
from backend.app.services.openai_image_analysis_service import OpenAIImageAnalysisService
from backend.app.services.openai_image_analysis_service import ImageAnalysisResult
from backend.app.services.rendered_scraping_service import RenderedScrapingService
from backend.app.services.scraping_service import PageMetadata
from backend.app.services.scraping_service import ScrapingService
from backend.app.services.site_extractors.chiba_aqualine_marathon_extractor import ChibaAqualineMarathonExtractor

logger = logging.getLogger(__name__)
SCRAPING_LOG_TEXT_LIMIT = 2000
JST = ZoneInfo("Asia/Tokyo")
PAGE_STATUS_AVAILABLE = "available"
PAGE_STATUS_PENDING = "pending"
PAGE_STATUS_ERROR = "error"


@dataclass(frozen=True)
class RaceCheckResult:
    race: Race
    changed: bool
    schedule_changed: bool
    failed: bool
    notification_events: tuple[str, ...]


class InvalidRaceUrlError(ValueError):
    pass


class InvalidRaceIdError(ValueError):
    pass


class RaceService:
    def __init__(self, db: Session) -> None:
        self.repository = RaceRepository(db)
        self.event_repository = RaceEventRepository(db)
        self.scraping_service = ScrapingService()
        self.rendered_scraping_service = RenderedScrapingService()
        self.openai_image_analysis_service = OpenAIImageAnalysisService()
        self.deadline_detection_service = DeadlineDetectionService()
        self.site_extractors = [
            ChibaAqualineMarathonExtractor(self.deadline_detection_service),
        ]

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
        metadata, detection, fetch_error, page_status = self._fetch_metadata_and_detect(normalized_url)
        title = self._select_title(metadata, fallback=source_domain)

        race = Race(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            registered_by=registered_by,
            title=title,
            url=normalized_url,
            source_domain=source_domain,
            page_status=page_status,
            entry_start_at=detection.entry_start_at,
            entry_deadline=detection.entry_deadline,
            entry_status=detection.entry_status,
            last_checked_at=checked_at,
            last_content_hash=metadata.content_hash if metadata else None,
            last_extraction_method=self._select_extraction_method(detection),
            last_detected_text=detection.detected_text,
        )

        try:
            self.repository.add(race)
            self._record_registration_event(
                race=race,
                detection=detection,
                fetch_error=fetch_error,
                page_status=page_status,
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

    def check_registered_race(self, race: Race) -> RaceCheckResult:
        checked_at = datetime.now(UTC)
        old_entry_start_at = race.entry_start_at
        old_entry_deadline = race.entry_deadline
        old_entry_status = race.entry_status
        old_page_status = race.page_status
        old_content_hash = race.last_content_hash
        old_extraction_method = race.last_extraction_method

        metadata, detection, fetch_error, page_status = self._fetch_metadata_and_detect_for_job(race)
        notification_events: list[str] = []
        schedule_changed = False

        race.last_checked_at = checked_at
        race.page_status = page_status

        if fetch_error:
            self.event_repository.add(
                race_id=race.id,
                event_type="page_pending" if page_status == PAGE_STATUS_PENDING else "check_failed",
                new_value=fetch_error,
            )
            self.repository.commit()
            self.repository.refresh(race)
            return RaceCheckResult(
                race=race,
                changed=old_page_status != race.page_status,
                schedule_changed=False,
                failed=True,
                notification_events=(),
            )

        entry_start_at = self._select_valid_schedule_value(
            old_value=old_entry_start_at,
            new_value=detection.entry_start_at,
        )
        entry_deadline = self._select_valid_schedule_value(
            old_value=old_entry_deadline,
            new_value=detection.entry_deadline,
        )

        if old_page_status in {PAGE_STATUS_PENDING, PAGE_STATUS_ERROR} and page_status == PAGE_STATUS_AVAILABLE:
            self.event_repository.add(
                race_id=race.id,
                event_type="page_available",
                old_value=old_page_status,
                new_value=page_status,
            )

        if old_content_hash and metadata and old_content_hash != metadata.content_hash:
            self.event_repository.add(
                race_id=race.id,
                event_type="page_changed",
                old_value=old_content_hash,
                new_value=metadata.content_hash,
            )

        old_schedule_key = self._schedule_key(
            entry_start_at=old_entry_start_at,
            entry_deadline=old_entry_deadline,
        )
        new_schedule_key = self._schedule_key(
            entry_start_at=entry_start_at,
            entry_deadline=entry_deadline,
        )

        had_old_schedule = old_entry_start_at is not None or old_entry_deadline is not None
        has_new_schedule = entry_start_at is not None or entry_deadline is not None
        if has_new_schedule and not had_old_schedule:
            self.event_repository.add(
                race_id=race.id,
                event_type="entry_schedule_detected",
                old_value=old_schedule_key,
                new_value=new_schedule_key,
            )
            schedule_changed = True
            notification_events.append("entry_schedule_detected")
        elif has_new_schedule and old_schedule_key != new_schedule_key:
            self.event_repository.add(
                race_id=race.id,
                event_type="entry_schedule_changed",
                old_value=old_schedule_key,
                new_value=new_schedule_key,
            )
            schedule_changed = True
            notification_events.append("entry_schedule_changed")

        if detection.entry_status == "closed" and old_entry_status != "closed":
            self.event_repository.add(
                race_id=race.id,
                event_type="entry_closed",
                old_value=old_entry_status,
                new_value=detection.detected_text,
            )

        race.title = self._select_title(metadata, fallback=race.title)
        race.entry_start_at = entry_start_at
        race.entry_deadline = entry_deadline
        race.entry_status = detection.entry_status
        race.last_content_hash = metadata.content_hash if metadata else None
        race.last_extraction_method = self._select_job_extraction_method(
            old_extraction_method=old_extraction_method,
            detection=detection,
        )
        race.last_detected_text = detection.detected_text

        changed = any(
            (
                old_entry_start_at != race.entry_start_at,
                old_entry_deadline != race.entry_deadline,
                old_entry_status != race.entry_status,
                old_page_status != race.page_status,
                old_content_hash != race.last_content_hash,
            )
        )

        self.repository.commit()
        self.repository.refresh(race)
        return RaceCheckResult(
            race=race,
            changed=changed,
            schedule_changed=schedule_changed,
            failed=False,
            notification_events=tuple(notification_events),
        )

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

    def _schedule_key(
        self,
        *,
        entry_start_at: datetime | None,
        entry_deadline: datetime | None,
    ) -> str:
        start_text = entry_start_at.isoformat() if entry_start_at else "-"
        deadline_text = entry_deadline.isoformat() if entry_deadline else "-"
        return f"start={start_text};deadline={deadline_text}"

    def _select_valid_schedule_value(
        self,
        *,
        old_value: datetime | None,
        new_value: datetime | None,
    ) -> datetime | None:
        if new_value is None:
            return old_value
        if old_value is None:
            return new_value
        if new_value < old_value:
            return old_value

        return new_value

    def _select_job_extraction_method(
        self,
        *,
        old_extraction_method: str | None,
        detection: DeadlineDetectionResult,
    ) -> str:
        if old_extraction_method in {"html", "llm"}:
            return old_extraction_method

        return self._select_extraction_method(detection)

    def _fetch_metadata_and_detect_for_job(
        self,
        race: Race,
    ) -> tuple[PageMetadata | None, DeadlineDetectionResult, str | None, str]:
        if race.last_extraction_method == "html":
            return self._fetch_metadata_and_detect_with_html(race.url)
        if race.last_extraction_method == "llm":
            return self._fetch_metadata_and_detect_with_llm(race.url)

        return self._fetch_metadata_and_detect(race.url)

    def _fetch_metadata_and_detect_with_html(
        self,
        url: str,
    ) -> tuple[PageMetadata | None, DeadlineDetectionResult, str | None, str]:
        static_metadata, static_error, static_page_status = self._fetch_static_metadata(url)
        static_detection = self._detect_deadline(static_metadata, url=url)
        if static_page_status == PAGE_STATUS_PENDING:
            return None, static_detection, static_error, PAGE_STATUS_PENDING

        if self._is_detection_complete(static_detection):
            return static_metadata, static_detection, None, PAGE_STATUS_AVAILABLE

        rendered_metadata, rendered_error = self._fetch_rendered_metadata(url)
        rendered_detection = self._detect_deadline(rendered_metadata, url=url)
        if rendered_metadata is not None:
            return rendered_metadata, rendered_detection, None, PAGE_STATUS_AVAILABLE

        if static_metadata is not None:
            return static_metadata, static_detection, None, PAGE_STATUS_AVAILABLE

        fetch_error = static_error or rendered_error or "failed to fetch page metadata"
        return None, static_detection, fetch_error, static_page_status

    def _fetch_metadata_and_detect_with_llm(
        self,
        url: str,
    ) -> tuple[PageMetadata | None, DeadlineDetectionResult, str | None, str]:
        rendered_metadata, rendered_error = self._fetch_rendered_metadata(url)
        if rendered_metadata is None:
            return None, self._empty_detection(), rendered_error, PAGE_STATUS_ERROR

        image_detection = self._detect_deadline_from_images(rendered_metadata)
        if image_detection is not None:
            return rendered_metadata, image_detection, None, PAGE_STATUS_AVAILABLE

        return rendered_metadata, self._empty_detection(), None, PAGE_STATUS_AVAILABLE

    def _empty_detection(self) -> DeadlineDetectionResult:
        return DeadlineDetectionResult(
            entry_start_at=None,
            entry_deadline=None,
            entry_status="unknown",
            detected_text=None,
        )

    def _fetch_metadata_and_detect(
        self,
        url: str,
    ) -> tuple[PageMetadata | None, DeadlineDetectionResult, str | None, str]:
        static_metadata, static_error, static_page_status = self._fetch_static_metadata(url)
        static_detection = self._detect_deadline(static_metadata, url=url)
        if static_page_status == PAGE_STATUS_PENDING:
            return None, static_detection, static_error, PAGE_STATUS_PENDING

        if self._is_detection_complete(static_detection):
            return static_metadata, static_detection, None, PAGE_STATUS_AVAILABLE

        rendered_metadata, rendered_error = self._fetch_rendered_metadata(url)
        rendered_detection = self._detect_deadline(rendered_metadata, url=url)
        if self._is_detection_complete(rendered_detection):
            return rendered_metadata, rendered_detection, None, PAGE_STATUS_AVAILABLE

        image_metadata = rendered_metadata or static_metadata
        fallback_detection = rendered_detection if rendered_metadata is not None else static_detection
        if self._should_try_image_analysis(fallback_detection):
            image_detection = self._detect_deadline_from_images(image_metadata)
            if image_detection is not None:
                return image_metadata, image_detection, None, PAGE_STATUS_AVAILABLE

        if rendered_metadata is not None:
            return rendered_metadata, rendered_detection, None, PAGE_STATUS_AVAILABLE

        if static_metadata is not None:
            return static_metadata, static_detection, None, PAGE_STATUS_AVAILABLE

        fetch_error = static_error or rendered_error or "failed to fetch page metadata"
        return None, static_detection, fetch_error, static_page_status

    def _fetch_static_metadata(self, url: str) -> tuple[PageMetadata | None, str | None, str]:
        try:
            return self.scraping_service.fetch_metadata(url), None, PAGE_STATUS_AVAILABLE
        except requests.RequestException as exc:
            return None, str(exc), self._page_status_for_request_exception(exc)

    def _page_status_for_request_exception(self, exc: requests.RequestException) -> str:
        if self._http_status_code(exc) == 404:
            return PAGE_STATUS_PENDING

        return PAGE_STATUS_ERROR

    def _http_status_code(self, exc: requests.RequestException) -> int | None:
        response = getattr(exc, "response", None)
        if response is None:
            return None

        return getattr(response, "status_code", None)

    def _fetch_rendered_metadata(self, url: str) -> tuple[PageMetadata | None, str | None]:
        try:
            return self.rendered_scraping_service.fetch_metadata(url), None
        except Exception as exc:
            logger.warning("playwright scraping failed url=%s error=%s", url, exc)
            return None, str(exc)

    def _select_title(self, metadata: PageMetadata | None, *, fallback: str) -> str:
        if metadata is None:
            return fallback
        if not metadata.title:
            return fallback

        return metadata.title[:255]

    def _detect_deadline(self, metadata: PageMetadata | None, *, url: str | None = None) -> DeadlineDetectionResult:
        if metadata is None:
            return DeadlineDetectionResult(
                entry_start_at=None,
                entry_deadline=None,
                entry_status="unknown",
                detected_text=None,
            )

        site_detection = self._detect_deadline_with_site_extractor(metadata=metadata, url=url)
        if site_detection is not None:
            self._log_local_scraping_detection(metadata=metadata, detection=site_detection)
            return site_detection

        detection = self.deadline_detection_service.detect(metadata.text)
        self._log_local_scraping_detection(metadata=metadata, detection=detection)
        return detection

    def _detect_deadline_with_site_extractor(
        self,
        *,
        metadata: PageMetadata,
        url: str | None,
    ) -> DeadlineDetectionResult | None:
        if url is None:
            return None

        for extractor in self.site_extractors:
            if not extractor.supports(url):
                continue

            detection = extractor.detect(metadata.text)
            if detection is not None:
                return detection

        return None

    def _detect_deadline_from_images(
        self,
        metadata: PageMetadata | None,
    ) -> DeadlineDetectionResult | None:
        if metadata is None:
            return None

        try:
            image_analysis = self.openai_image_analysis_service.analyze(metadata)
        except Exception as exc:
            logger.warning("openai image analysis failed source=%s error=%s", metadata.source_method, exc)
            return None

        if image_analysis is None:
            return None

        detection = self._build_detection_from_image_analysis(image_analysis)
        image_metadata = self._build_image_analysis_metadata(metadata=metadata, image_analysis=image_analysis)
        self._log_local_scraping_detection(metadata=image_metadata, detection=detection)
        return detection

    def _build_detection_from_image_analysis(
        self,
        image_analysis: ImageAnalysisResult,
    ) -> DeadlineDetectionResult:
        entry_start_at = self._build_date_datetime(image_analysis.entry_start_date, day_end=False)
        entry_deadline = self._build_date_datetime(image_analysis.entry_deadline_date, day_end=True)
        detected_text = self._build_image_analysis_detected_text(image_analysis)

        return DeadlineDetectionResult(
            entry_start_at=entry_start_at,
            entry_deadline=entry_deadline,
            entry_status=self._select_image_analysis_entry_status(
                image_analysis=image_analysis,
                entry_deadline=entry_deadline,
            ),
            detected_text=detected_text,
        )

    def _build_image_analysis_metadata(
        self,
        *,
        metadata: PageMetadata,
        image_analysis: ImageAnalysisResult,
    ) -> PageMetadata:
        return PageMetadata(
            title=metadata.title,
            text=self._build_image_analysis_detected_text(image_analysis) or "",
            image_urls=metadata.image_urls,
            images=metadata.images,
            source_method="openai_vision",
            screenshot_base64=metadata.screenshot_base64,
            content_hash=metadata.content_hash,
        )

    def _build_date_datetime(self, date_text: str | None, *, day_end: bool) -> datetime | None:
        if date_text is None:
            return None

        try:
            detected_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            return None

        return datetime.combine(
            detected_date,
            time(23, 59, 59) if day_end else time(0, 0, 0),
            tzinfo=JST,
        )

    def _select_image_analysis_entry_status(
        self,
        *,
        image_analysis: ImageAnalysisResult,
        entry_deadline: datetime | None,
    ) -> str:
        if image_analysis.entry_status == "closed":
            return "closed"

        if entry_deadline is not None:
            return "open"

        return image_analysis.entry_status

    def _build_image_analysis_detected_text(self, image_analysis: ImageAnalysisResult) -> str | None:
        parts = [
            f"entry_start_date={image_analysis.entry_start_date}" if image_analysis.entry_start_date else None,
            f"entry_deadline_date={image_analysis.entry_deadline_date}" if image_analysis.entry_deadline_date else None,
            f"entry_status={image_analysis.entry_status}",
            image_analysis.evidence_text,
        ]
        text = " ".join(part for part in parts if part)
        if not text:
            return None

        return f"[openai_vision] {text}"

    def _is_detection_complete(self, detection: DeadlineDetectionResult) -> bool:
        return detection.entry_start_at is not None or detection.entry_deadline is not None

    def _should_try_image_analysis(self, detection: DeadlineDetectionResult) -> bool:
        return detection.entry_start_at is None and detection.entry_deadline is None

    def _select_extraction_method(self, detection: DeadlineDetectionResult) -> str:
        if detection.detected_text and detection.detected_text.startswith("[openai_vision]"):
            return "llm"

        return "html"

    def _log_local_scraping_detection(
        self,
        *,
        metadata: PageMetadata,
        detection: DeadlineDetectionResult,
    ) -> None:
        if not is_local_env():
            return

        logger.info(
            "[local scraping] detection input source=%s title=%r text_excerpt=%r",
            metadata.source_method,
            metadata.title,
            metadata.text[:SCRAPING_LOG_TEXT_LIMIT],
        )
        logger.info(
            "[local scraping] detection result entry_start_at=%s entry_deadline=%s entry_status=%s detected_text=%r",
            detection.entry_start_at,
            detection.entry_deadline,
            detection.entry_status,
            detection.detected_text,
        )

    def _record_registration_event(
        self,
        *,
        race: Race,
        detection: DeadlineDetectionResult,
        fetch_error: str | None,
        page_status: str,
    ) -> None:
        if fetch_error:
            self.event_repository.add(
                race_id=race.id,
                event_type="page_pending" if page_status == PAGE_STATUS_PENDING else "check_failed",
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
