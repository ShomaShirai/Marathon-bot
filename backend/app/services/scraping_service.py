from dataclasses import dataclass
import hashlib
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


MAX_TEXT_LENGTH = 30000
MAX_IMAGE_CONTEXT_LENGTH = 300
USER_AGENT = "MarathonBot/0.1"


@dataclass(frozen=True)
class PageImage:
    url: str
    alt: str | None = None
    context: str | None = None


@dataclass(frozen=True)
class PageMetadata:
    title: str | None
    text: str
    image_urls: tuple[str, ...] = ()
    images: tuple[PageImage, ...] = ()
    source_method: str = "static_html"
    screenshot_base64: str | None = None
    content_hash: str | None = None


class ScrapingService:
    def fetch_metadata(self, url: str) -> PageMetadata:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=2,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title_tag = soup.find("title")
        title = title_tag.get_text(" ", strip=True) if title_tag else None
        images = self._extract_images(soup=soup, base_url=url)
        text = self._build_text(soup=soup, title=title, images=images)

        return PageMetadata(
            title=title,
            text=text[:MAX_TEXT_LENGTH],
            image_urls=tuple(image.url for image in images),
            images=tuple(images),
            content_hash=self._hash_text(text),
        )

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _build_text(
        self,
        *,
        soup: BeautifulSoup,
        title: str | None,
        images: list[PageImage],
    ) -> str:
        parts: list[str] = []

        if title:
            parts.append(title)

        description = soup.find("meta", attrs={"name": "description"})
        if description and description.get("content"):
            parts.append(str(description["content"]))

        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            parts.append(str(og_title["content"]))

        parts.append(soup.get_text(" ", strip=True))

        for image in images:
            image_parts = [image.alt, image.context, image.url]
            parts.append(" ".join(part for part in image_parts if part))

        return self._normalize_text(" ".join(parts))

    def _extract_images(self, *, soup: BeautifulSoup, base_url: str) -> list[PageImage]:
        images: list[PageImage] = []
        seen_urls: set[str] = set()

        for image_tag in soup.find_all("img"):
            raw_url = image_tag.get("src") or image_tag.get("data-src")
            if not raw_url:
                continue

            image_url = urljoin(base_url, str(raw_url))
            if image_url in seen_urls:
                continue

            seen_urls.add(image_url)
            alt = self._normalize_text(str(image_tag.get("alt") or "")) or None
            parent_text = (
                self._normalize_text(image_tag.parent.get_text(" ", strip=True))
                if image_tag.parent
                else ""
            )
            context = parent_text[:MAX_IMAGE_CONTEXT_LENGTH] or None
            images.append(PageImage(url=image_url, alt=alt, context=context))

        return images

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
