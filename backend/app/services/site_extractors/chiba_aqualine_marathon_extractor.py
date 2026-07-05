from datetime import datetime
import re
from urllib.parse import urlparse

from backend.app.services.deadline_detection_service import DeadlineDetectionResult
from backend.app.services.deadline_detection_service import DeadlineDetectionService

TARGET_HOST = "chiba-aqualine-marathon.com"
TARGET_PATH = "/runner/entry.html"
TARGET_SECTION_TITLE = "学生応援枠"
NEXT_SECTION_TITLES = (
    "千葉県民先行枠",
    "一般枠",
)


class ChibaAqualineMarathonExtractor:
    def __init__(self, deadline_detection_service: DeadlineDetectionService) -> None:
        self.deadline_detection_service = deadline_detection_service

    def supports(self, url: str) -> bool:
        parsed_url = urlparse(url)
        return parsed_url.hostname == TARGET_HOST and parsed_url.path == TARGET_PATH

    def detect(self, text: str, *, now: datetime | None = None) -> DeadlineDetectionResult | None:
        section_text = self._extract_student_support_section(text)
        if section_text is None:
            return None

        page_year = self._extract_page_year(text)
        normalized_text = self._normalize_section_text(section_text, page_year=page_year)
        detection = self.deadline_detection_service.detect(normalized_text, now=now)
        if detection.entry_start_at is None and detection.entry_deadline is None:
            return None

        return detection

    def _normalize_section_text(self, section_text: str, *, page_year: int | None) -> str:
        normalized_text = section_text.replace("募集期間", "申込期間")

        def replace_month_day(match: re.Match[str]) -> str:
            month = match.group("month")
            day = match.group("day")
            if page_year is None:
                return f"{month}月{day}日"

            return f"{page_year}年{month}月{day}日"

        return re.sub(
            r"(?<!\d)(?P<month>\d{1,2})/(?P<day>\d{1,2})(?!\d)",
            replace_month_day,
            normalized_text,
        )

    def _extract_page_year(self, text: str) -> int | None:
        match = re.search(r"20\d{2}", text)
        if match is None:
            return None

        return int(match.group(0))

    def _extract_student_support_section(self, text: str) -> str | None:
        section_start = text.find(TARGET_SECTION_TITLE)
        if section_start < 0:
            return None

        next_section_start = self._find_next_section_start(text, section_start + len(TARGET_SECTION_TITLE))
        section_text = text[section_start:next_section_start].strip()
        if not section_text:
            return None

        return section_text

    def _find_next_section_start(self, text: str, search_start: int) -> int:
        next_section_starts = [
            section_start
            for section_title in NEXT_SECTION_TITLES
            if (section_start := text.find(section_title, search_start)) >= 0
        ]
        if not next_section_starts:
            return len(text)

        return min(next_section_starts)
