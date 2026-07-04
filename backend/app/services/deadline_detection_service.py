from dataclasses import dataclass
from datetime import datetime, time
import re
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
KEYWORDS = (
    "エントリー開始",
    "申込開始",
    "申し込み開始",
    "受付開始",
    "募集開始",
    "エントリー締切",
    "申込締切",
    "申し込み締切",
    "募集締切",
    "受付終了",
    "申込期間",
    "エントリー期間",
    "締切",
)
PERIOD_KEYWORDS = {"申込期間", "エントリー期間"}
START_KEYWORDS = {"エントリー開始", "申込開始", "申し込み開始", "受付開始", "募集開始"}
WINDOW_SIZE = 120


@dataclass(frozen=True)
class DeadlineDetectionResult:
    entry_start_at: datetime | None
    entry_deadline: datetime | None
    entry_status: str
    detected_text: str | None


@dataclass(frozen=True)
class DateCandidate:
    value: datetime
    start: int
    end: int
    text: str


class DeadlineDetectionService:
    def detect(self, text: str, *, now: datetime | None = None) -> DeadlineDetectionResult:
        normalized_text = re.sub(r"\s+", " ", text).strip()
        if not normalized_text:
            return DeadlineDetectionResult(
                entry_start_at=None,
                entry_deadline=None,
                entry_status="unknown",
                detected_text=None,
            )

        now_jst = now.astimezone(JST) if now else datetime.now(JST)
        keyword_matches = list(self._find_keyword_matches(normalized_text))
        if not keyword_matches:
            return DeadlineDetectionResult(
                entry_start_at=None,
                entry_deadline=None,
                entry_status="unknown",
                detected_text=normalized_text[:500],
            )

        best_deadline_candidate: tuple[int, int, DateCandidate, str] | None = None
        best_start_candidate: tuple[int, int, DateCandidate, str] | None = None
        closed_detected_text: str | None = None

        for keyword_start, keyword in keyword_matches:
            window_start = max(0, keyword_start - WINDOW_SIZE)
            window_end = min(len(normalized_text), keyword_start + len(keyword) + WINDOW_SIZE)
            window_text = normalized_text[window_start:window_end]
            candidates = self._find_date_candidates(
                window_text,
                base_offset=window_start,
                now=now_jst,
            )

            if keyword == "受付終了" and not candidates:
                closed_detected_text = window_text[:500]
                continue

            selected = self._select_candidates(keyword, keyword_start, candidates)
            if selected is None:
                continue

            start_candidate, deadline_candidate = selected
            if start_candidate is not None:
                distance = abs(start_candidate.start - keyword_start)
                if best_start_candidate is None or distance < best_start_candidate[0]:
                    best_start_candidate = (distance, distance, start_candidate, window_text[:500])

            if deadline_candidate is not None:
                distance = abs(deadline_candidate.start - keyword_start)
                score = 0 if keyword in PERIOD_KEYWORDS else distance
                if best_deadline_candidate is None or score < best_deadline_candidate[0]:
                    best_deadline_candidate = (score, distance, deadline_candidate, window_text[:500])

        if best_start_candidate is not None or best_deadline_candidate is not None:
            start_candidate = best_start_candidate[2] if best_start_candidate else None
            deadline_candidate = best_deadline_candidate[2] if best_deadline_candidate else None
            detected_text = (
                best_deadline_candidate[3]
                if best_deadline_candidate is not None
                else best_start_candidate[3]
            )

            return DeadlineDetectionResult(
                entry_start_at=start_candidate.value if start_candidate else None,
                entry_deadline=deadline_candidate.value if deadline_candidate else None,
                entry_status="open" if deadline_candidate else "unknown",
                detected_text=detected_text,
            )

        if closed_detected_text:
            return DeadlineDetectionResult(
                entry_start_at=None,
                entry_deadline=None,
                entry_status="closed",
                detected_text=closed_detected_text,
            )

        return DeadlineDetectionResult(
            entry_start_at=None,
            entry_deadline=None,
            entry_status="unknown",
            detected_text=normalized_text[:500],
        )

    def _find_keyword_matches(self, text: str) -> list[tuple[int, str]]:
        matches: list[tuple[int, str]] = []
        for keyword in KEYWORDS:
            matches.extend((match.start(), keyword) for match in re.finditer(re.escape(keyword), text))
        return sorted(matches)

    def _find_date_candidates(
        self,
        text: str,
        *,
        base_offset: int,
        now: datetime,
    ) -> list[DateCandidate]:
        candidates: list[DateCandidate] = []
        seen_spans: list[tuple[int, int]] = []

        patterns = (
            re.compile(r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"),
            re.compile(r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"),
            re.compile(r"(?<!\d)(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"),
        )

        for pattern in patterns:
            for match in pattern.finditer(text):
                span = (base_offset + match.start(), base_offset + match.end())
                if self._overlaps_existing_span(span, seen_spans):
                    continue

                end_of_day = self._build_datetime(match, now, day_end=True)
                start_of_day = self._build_datetime(match, now, day_end=False)
                if end_of_day is None or start_of_day is None:
                    continue

                seen_spans.append(span)
                candidates.append(
                    DateCandidate(
                        value=end_of_day,
                        start=span[0],
                        end=span[1],
                        text=match.group(0),
                    )
                )

        return sorted(candidates, key=lambda candidate: candidate.start)

    def _overlaps_existing_span(
        self,
        span: tuple[int, int],
        existing_spans: list[tuple[int, int]],
    ) -> bool:
        start, end = span
        return any(start < existing_end and end > existing_start for existing_start, existing_end in existing_spans)

    def _build_datetime(
        self,
        match: re.Match[str],
        now: datetime,
        *,
        day_end: bool,
    ) -> datetime | None:
        year = int(match.groupdict().get("year") or now.year)
        month = int(match.group("month"))
        day = int(match.group("day"))

        try:
            detected_at = datetime.combine(
                datetime(year, month, day).date(),
                time(23, 59, 59) if day_end else time(0, 0, 0),
                tzinfo=JST,
            )
        except ValueError:
            return None

        if "year" not in match.groupdict() and detected_at < now:
            try:
                detected_at = detected_at.replace(year=detected_at.year + 1)
            except ValueError:
                return None

        return detected_at

    def _select_candidates(
        self,
        keyword: str,
        keyword_start: int,
        candidates: list[DateCandidate],
    ) -> tuple[DateCandidate | None, DateCandidate | None] | None:
        if not candidates:
            return None

        if keyword in PERIOD_KEYWORDS and len(candidates) >= 2:
            start_candidate = candidates[0]
            deadline_candidate = candidates[-1]
            return (
                self._as_start_candidate(start_candidate),
                deadline_candidate,
            )

        candidate = min(candidates, key=lambda candidate: abs(candidate.start - keyword_start))

        if keyword in START_KEYWORDS:
            return (self._as_start_candidate(candidate), None)

        return (None, candidate)

    def _as_start_candidate(self, candidate: DateCandidate) -> DateCandidate:
        return DateCandidate(
            value=candidate.value.replace(hour=0, minute=0, second=0, microsecond=0),
            start=candidate.start,
            end=candidate.end,
            text=candidate.text,
        )
