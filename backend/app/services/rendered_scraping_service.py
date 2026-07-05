from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from backend.app.services.scraping_service import MAX_TEXT_LENGTH
from backend.app.services.scraping_service import PageImage
from backend.app.services.scraping_service import PageMetadata
from backend.app.services.scraping_service import PageTextRegion
from backend.app.services.scraping_service import USER_AGENT

NAVIGATION_TIMEOUT_MS = 15000
SCREENSHOT_TIMEOUT_MS = 5000
MAX_IMAGE_CONTEXT_LENGTH = 300
DEADLINE_CONTEXT_KEYWORDS = (
    "エントリー",
    "申込",
    "申し込み",
    "募集",
    "締切",
    "受付",
    "申込期間",
    "エントリー期間",
)


class RenderedScrapingService:
    def fetch_metadata(self, url: str) -> PageMetadata:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
                page.goto(url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT_MS)

                title = page.title() or None
                body_text = page.locator("body").inner_text(timeout=NAVIGATION_TIMEOUT_MS)
                images = self._extract_images(page)
                deadline_regions = self._extract_deadline_regions(page)
                screenshot_base64 = self._take_screenshot(page)

                text_parts = [title, body_text]
                for image in images:
                    text_parts.append(" ".join(part for part in (image.alt, image.context, image.url) if part))

                text = self._normalize_text(" ".join(part for part in text_parts if part))
                return PageMetadata(
                    title=title,
                    text=text[:MAX_TEXT_LENGTH],
                    image_urls=tuple(image.url for image in images),
                    images=tuple(images),
                    deadline_regions=tuple(deadline_regions),
                    source_method="playwright",
                    screenshot_base64=screenshot_base64,
                    content_hash=self._hash_text(text),
                )
            finally:
                browser.close()

    def _extract_images(self, page: Any) -> list[PageImage]:
        raw_images = page.evaluate(
            """
            () => Array.from(document.images).map((img) => ({
                url: img.currentSrc || img.src || "",
                alt: img.alt || "",
                context: img.parentElement ? img.parentElement.innerText || "" : "",
                naturalWidth: img.naturalWidth || 0,
                naturalHeight: img.naturalHeight || 0,
                rect: (() => {
                    const rect = img.getBoundingClientRect();
                    return {
                        x: rect.x + window.scrollX,
                        y: rect.y + window.scrollY,
                        width: rect.width,
                        height: rect.height
                    };
                })()
            }))
            """
        )

        images: list[PageImage] = []
        seen_urls: set[str] = set()
        for raw_image in raw_images:
            url = self._normalize_text(str(raw_image.get("url") or ""))
            if not url or url in seen_urls:
                continue

            rect = raw_image.get("rect") or {}
            width = float(rect.get("width") or raw_image.get("naturalWidth") or 0)
            height = float(rect.get("height") or raw_image.get("naturalHeight") or 0)
            if width < 120 or height < 40:
                continue

            seen_urls.add(url)
            alt = self._normalize_text(str(raw_image.get("alt") or "")) or None
            context = self._normalize_text(str(raw_image.get("context") or ""))[:MAX_IMAGE_CONTEXT_LENGTH] or None
            images.append(
                PageImage(
                    url=url,
                    alt=alt,
                    context=context,
                    x=float(rect.get("x") or 0),
                    y=float(rect.get("y") or 0),
                    width=width,
                    height=height,
                )
            )

        return images

    def _extract_deadline_regions(self, page: Any) -> list[PageTextRegion]:
        raw_regions = page.evaluate(
            """
            (keywords) => Array.from(document.querySelectorAll("body *"))
                .map((element) => {
                    const text = (element.innerText || "").replace(/\\s+/g, " ").trim();
                    if (!text || text.length > 500) return null;
                    if (!keywords.some((keyword) => text.includes(keyword))) return null;
                    const rect = element.getBoundingClientRect();
                    if (!rect.width || !rect.height) return null;
                    return {
                        text,
                        x: rect.x + window.scrollX,
                        y: rect.y + window.scrollY,
                        width: rect.width,
                        height: rect.height
                    };
                })
                .filter(Boolean)
            """,
            list(DEADLINE_CONTEXT_KEYWORDS),
        )

        regions: list[PageTextRegion] = []
        seen_regions: set[tuple[str, int, int]] = set()
        for raw_region in raw_regions:
            text = self._normalize_text(str(raw_region.get("text") or ""))
            x = float(raw_region.get("x") or 0)
            y = float(raw_region.get("y") or 0)
            key = (text[:120], int(x), int(y))
            if not text or key in seen_regions:
                continue

            seen_regions.add(key)
            regions.append(
                PageTextRegion(
                    text=text[:MAX_IMAGE_CONTEXT_LENGTH],
                    x=x,
                    y=y,
                    width=float(raw_region.get("width") or 0),
                    height=float(raw_region.get("height") or 0),
                )
            )

        return regions

    def _take_screenshot(self, page: Any) -> str | None:
        try:
            screenshot = page.screenshot(full_page=False, timeout=SCREENSHOT_TIMEOUT_MS)
        except Exception:
            return None

        return base64.b64encode(screenshot).decode("ascii")

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
