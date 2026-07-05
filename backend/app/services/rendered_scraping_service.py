from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from backend.app.services.scraping_service import MAX_TEXT_LENGTH
from backend.app.services.scraping_service import PageImage
from backend.app.services.scraping_service import PageMetadata
from backend.app.services.scraping_service import USER_AGENT

NAVIGATION_TIMEOUT_MS = 15000
SCREENSHOT_TIMEOUT_MS = 5000
MAX_IMAGE_CONTEXT_LENGTH = 300


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
                width: img.naturalWidth || img.width || 0,
                height: img.naturalHeight || img.height || 0
            }))
            """
        )

        images: list[PageImage] = []
        seen_urls: set[str] = set()
        for raw_image in raw_images:
            url = self._normalize_text(str(raw_image.get("url") or ""))
            if not url or url in seen_urls:
                continue

            width = int(raw_image.get("width") or 0)
            height = int(raw_image.get("height") or 0)
            if width < 120 or height < 40:
                continue

            seen_urls.add(url)
            alt = self._normalize_text(str(raw_image.get("alt") or "")) or None
            context = self._normalize_text(str(raw_image.get("context") or ""))[:MAX_IMAGE_CONTEXT_LENGTH] or None
            images.append(PageImage(url=url, alt=alt, context=context))

        return images

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
