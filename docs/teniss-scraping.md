```python
import argparse
import csv
import hashlib
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

"""
Playwrightでテニスベアの大会一覧を描画してから、DB投入用CSVを生成する。

保存済みの debug_tennisbear_playwright.html からも抽出できるようにしている。
"""

BASE_DIR = Path(__file__).resolve().parent

TENNIS_BEAR_BASE_URL = "https://www.tennisbear.net"
TENNIS_TOURNAMENT_SOURCE_URL = f"{TENNIS_BEAR_BASE_URL}/tournament/prefecture/pref12"
OUTPUT_CSV = BASE_DIR / "tennis_tournaments.csv"
DEBUG_HTML_PATH = BASE_DIR / "debug_tennisbear_playwright.html"
DEBUG_SCREENSHOT_PATH = BASE_DIR / "debug_tennisbear_playwright.png"

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
TIMEZONE = ZoneInfo("Asia/Tokyo")

CSV_FIELDNAMES = [
    "category",
    "registered_by",
    "title",
    "url",
    "source_domain",
    "page_status",
    "entry_start_at",
    "entry_deadline",
    "entry_status",
    "last_checked_at",
    "last_content_hash",
    "last_extraction_method",
    "last_detected_text",
]

EVENT_HREF_PATTERN = re.compile(r"^/event/\d+/info$")
EVENT_DATE_PATTERN = re.compile(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:\([^)]+\))?")
EVENT_TIME_PATTERN = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")

LOGGER = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def clean_text(text: str) -> str:
    return " ".join(text.split())


def add_months(value: date, months: int) -> date:
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


def log_browser_response(response) -> None:
    url = response.url
    if "tennisbear.net" not in url:
        return

    resource_type = response.request.resource_type
    if resource_type not in {"document", "xhr", "fetch"}:
        return

    LOGGER.info(
        "browser response status=%s type=%s url=%s",
        response.status,
        resource_type,
        url,
    )


def import_playwright() -> tuple[Any, Any]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Playwright is not installed. Run: "
            "scripts/venv/bin/python -m pip install -r scripts/requirements.txt"
        ) from e

    return sync_playwright, PlaywrightTimeoutError


def wait_for_rendered_content(page: Any, timeout_error: Any) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=30_000)
    except timeout_error:
        LOGGER.warning("network idle wait timed out; continuing with current DOM")

    for keyword in WAIT_KEYWORDS:
        try:
            page.wait_for_selector(f"text={keyword}", timeout=5_000)
            LOGGER.info("render keyword found keyword=%s", keyword)
            return
        except timeout_error:
            LOGGER.info("render keyword not found yet keyword=%s", keyword)

    LOGGER.warning("no target keyword appeared before timeout; continuing for diagnostics")


def scroll_to_load_more(page: Any, rounds: int = 8) -> None:
    previous_height = 0
    stable_rounds = 0

    for index in range(rounds):
        height = page.evaluate("document.body.scrollHeight")
        LOGGER.info("scroll round=%s height=%s", index + 1, height)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_500)

        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0

        previous_height = new_height
        if stable_rounds >= 2:
            LOGGER.info("scroll stopped because page height is stable")
            break


def diagnose_rendered_html(html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    LOGGER.info("rendered html length=%s text_length=%s", len(html), len(text))
    for keyword in WAIT_KEYWORDS:
        LOGGER.info("rendered keyword count keyword=%s count=%s", keyword, text.count(keyword))

    anchors = soup.find_all("a", href=lambda href: href and EVENT_HREF_PATTERN.match(href))
    LOGGER.info("rendered event info anchors=%s", len(anchors))
    for anchor in anchors[:30]:
        LOGGER.info("rendered event link=%s text=%s", anchor["href"], clean_text(anchor.get_text(" ", strip=True))[:160])


def fetch_rendered_html(url: str) -> str:
    LOGGER.info("playwright fetch url=%s", url)
    sync_playwright, timeout_error = import_playwright()

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as e:
            message = str(e)
            if "Executable doesn't exist" in message:
                raise RuntimeError(
                    "Playwright browser executable is missing. Run: "
                    "scripts/venv/bin/python -m playwright install chromium"
                ) from e
            if "error while loading shared libraries" in message:
                raise RuntimeError(
                    "Chromium is installed, but OS libraries required by Chromium are missing. "
                    "Run on the host machine: "
                    "scripts/venv/bin/python -m playwright install-deps chromium"
                ) from e
            raise

        context = browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.on("response", log_browser_response)

        response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        LOGGER.info(
            "document response status=%s final_url=%s",
            response.status if response else "",
            page.url,
        )

        wait_for_rendered_content(page, timeout_error)
        scroll_to_load_more(page)
        html = page.content()

        DEBUG_HTML_PATH.write_text(html, encoding="utf-8")
        LOGGER.info("saved debug html path=%s length=%s", DEBUG_HTML_PATH, len(html))

        page.screenshot(path=str(DEBUG_SCREENSHOT_PATH), full_page=True)
        LOGGER.info("saved debug screenshot path=%s", DEBUG_SCREENSHOT_PATH)

        context.close()
        browser.close()

    diagnose_rendered_html(html)
    return html


def parse_event_datetime(date_text: str, time_text: str, today: date | None = None) -> datetime | None:
    today = today or datetime.now(TIMEZONE).date()
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
        tzinfo=TIMEZONE,
    )


def get_direct_column_texts(anchor: Tag) -> list[list[str]]:
    row = anchor.find("div", class_=lambda value: value and "row" in value.split())
    if not isinstance(row, Tag):
        return []

    columns = []
    for child in row.children:
        if not isinstance(child, Tag):
            continue
        texts = [clean_text(text) for text in child.stripped_strings]
        texts = [text for text in texts if text]
        columns.append(texts)
    return columns


def parse_title_and_location(detail_texts: list[str]) -> tuple[str, str]:
    for index, text in enumerate(detail_texts):
        if text.startswith("千葉県"):
            title = detail_texts[index - 1] if index > 0 else ""
            return title, text
    if len(detail_texts) >= 2:
        return detail_texts[-1], ""
    if len(detail_texts) == 1:
        return detail_texts[0], ""
    return "", ""


def build_content_hash(record: dict) -> str:
    content = "|".join(
        [
            record["url"],
            record["title"],
            record["entry_deadline"],
            record["last_detected_text"],
        ]
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_event_anchor(
    anchor: Tag,
    checked_at: datetime | None = None,
    today: date | None = None,
) -> dict | None:
    href = anchor.get("href", "")
    if not EVENT_HREF_PATTERN.match(href):
        return None

    raw_text = clean_text(anchor.get_text(" ", strip=True))
    columns = get_direct_column_texts(anchor)
    if len(columns) < 4:
        LOGGER.debug("skip reason=invalid_column_count href=%s text=%s", href, raw_text[:160])
        return None

    registered_by = clean_text(" ".join(columns[0]))
    date_text = columns[1][0] if len(columns[1]) >= 1 else ""
    time_text = columns[1][1] if len(columns[1]) >= 2 else ""
    title, location = parse_title_and_location(columns[2])
    level = clean_text(" ".join(columns[3]))
    event_datetime = parse_event_datetime(date_text, time_text, today=today)
    if not event_datetime:
        LOGGER.debug("skip reason=datetime_parse_failed href=%s text=%s", href, raw_text[:160])
        return None

    checked_at = checked_at or datetime.now(TIMEZONE)
    entry_status = "closed" if any(keyword in raw_text for keyword in FULL_TOURNAMENT_KEYWORDS) else "open"
    record = {
        "category": "tennis",
        "registered_by": registered_by,
        "title": title,
        "url": urljoin(TENNIS_BEAR_BASE_URL, href),
        "source_domain": "www.tennisbear.net",
        "page_status": "active",
        "entry_start_at": "",
        "entry_deadline": event_datetime.isoformat(),
        "entry_status": entry_status,
        "last_checked_at": checked_at.isoformat(),
        "last_content_hash": "",
        "last_extraction_method": "playwright_html",
        "last_detected_text": raw_text,
        "_level": level,
        "_location": location,
    }
    record["last_content_hash"] = build_content_hash(record)
    return record


def extract_records_from_html(html: str, today: date | None = None) -> list[dict]:
    today = today or datetime.now(TIMEZONE).date()
    until = add_months(today, 3)
    checked_at = datetime.now(TIMEZONE)
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=lambda href: href and EVENT_HREF_PATTERN.match(href))
    LOGGER.info("event anchors=%s filter_from=%s filter_until=%s", len(anchors), today.isoformat(), until.isoformat())

    records = []
    seen_urls = set()
    for anchor in anchors:
        record = parse_event_anchor(anchor, checked_at=checked_at, today=today)
        if not record:
            continue

        deadline = datetime.fromisoformat(record["entry_deadline"])
        if deadline.date() < today or deadline.date() > until:
            LOGGER.debug("skip reason=date_out_of_range date=%s url=%s", deadline.date(), record["url"])
            continue

        if not any(keyword in record["last_detected_text"] for keyword in TARGET_LEVEL_KEYWORDS):
            LOGGER.debug("skip reason=level_not_matched title=%s url=%s", record["title"], record["url"])
            continue

        matched_exclude_keyword = next(
            (keyword for keyword in EXCLUDE_EVENT_KEYWORDS if keyword in record["last_detected_text"]),
            "",
        )
        if matched_exclude_keyword:
            LOGGER.debug(
                "skip reason=excluded_keyword keyword=%s title=%s url=%s",
                matched_exclude_keyword,
                record["title"],
                record["url"],
            )
            continue

        if record["url"] in seen_urls:
            LOGGER.debug("skip reason=duplicate url=%s", record["url"])
            continue

        seen_urls.add(record["url"])
        records.append({field: record[field] for field in CSV_FIELDNAMES})

    LOGGER.info("extracted records=%s", len(records))
    return records


def save_records_csv(records: list[dict], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)
    LOGGER.info("saved csv path=%s records=%s", output_path, len(records))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Tennis Bear event records.")
    parser.add_argument(
        "--html",
        type=Path,
        help="Use a saved rendered HTML file instead of launching Playwright.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_CSV,
        help=f"CSV output path. Default: {OUTPUT_CSV}",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()

    if args.html:
        html = args.html.read_text(encoding="utf-8")
        LOGGER.info("loaded html path=%s length=%s", args.html, len(html))
        diagnose_rendered_html(html)
    else:
        html = fetch_rendered_html(TENNIS_TOURNAMENT_SOURCE_URL)

    records = extract_records_from_html(html)
    save_records_csv(records, args.output)
    print(f"Saved {len(records)} tennis records to {args.output}")


if __name__ == "__main__":
    main()

```