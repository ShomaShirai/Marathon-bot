from dataclasses import dataclass
from datetime import datetime, time
import re
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
KEYWORDS = (
    "エントリー締切",
    "申込締切",
    "申し込み締切",
    "募集締切",
    "受付終了",
    "申込期間",
    "エントリー期間",
    "締切",
)
WINDOW_SIZE = 120


@dataclass(frozen=True)
class DeadlineDetectionResult:
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
                entry_deadline=None,
                entry_status="unknown",
                detected_text=None,
            )

        now_jst = now.astimezone(JST) if now else datetime.now(JST)
        keyword_matches = list(self._find_keyword_matches(normalized_text))
        if not keyword_matches:
            return DeadlineDetectionResult(
                entry_deadline=None,
                entry_status="unknown",
                detected_text=normalized_text[:500],
            )

        best_candidate: tuple[int, int, DateCandidate, str] | None = None
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

            candidate = self._select_candidate(keyword, keyword_start, candidates)
            if candidate is None:
                continue

            distance = abs(candidate.start - keyword_start)
            score = 0 if keyword in {"申込期間", "エントリー期間"} else distance
            if best_candidate is None or score < best_candidate[0]:
                best_candidate = (score, distance, candidate, window_text[:500])

        if best_candidate is not None:
            candidate = best_candidate[2]
            return DeadlineDetectionResult(
                entry_deadline=candidate.value,
                entry_status="open",
                detected_text=best_candidate[3],
            )

        if closed_detected_text:
            return DeadlineDetectionResult(
                entry_deadline=None,
                entry_status="closed",
                detected_text=closed_detected_text,
            )

        return DeadlineDetectionResult(
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

                deadline = self._build_deadline(match, now)
                if deadline is None:
                    continue

                seen_spans.append(span)
                candidates.append(
                    DateCandidate(
                        value=deadline,
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

    def _build_deadline(self, match: re.Match[str], now: datetime) -> datetime | None:
        year = int(match.groupdict().get("year") or now.year)
        month = int(match.group("month"))
        day = int(match.group("day"))

        try:
            deadline = datetime.combine(
                datetime(year, month, day).date(),
                time(23, 59, 59),
                tzinfo=JST,
            )
        except ValueError:
            return None

        if "year" not in match.groupdict() and deadline < now:
            try:
                deadline = deadline.replace(year=deadline.year + 1)
            except ValueError:
                return None

        return deadline

    def _select_candidate(
        self,
        keyword: str,
        keyword_start: int,
        candidates: list[DateCandidate],
    ) -> DateCandidate | None:
        if not candidates:
            return None

        if keyword in {"申込期間", "エントリー期間"} and len(candidates) >= 2:
            return candidates[-1]

        return min(candidates, key=lambda candidate: abs(candidate.start - keyword_start))
