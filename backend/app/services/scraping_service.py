from dataclasses import dataclass
import re

import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class PageMetadata:
    title: str | None
    text: str


class ScrapingService:
    def fetch_metadata(self, url: str) -> PageMetadata:
        response = requests.get(
            url,
            headers={"User-Agent": "MarathonBot/0.1"},
            timeout=2,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title_tag = soup.find("title")
        title = title_tag.get_text(" ", strip=True) if title_tag else None
        text = self._normalize_text(soup.get_text(" ", strip=True))

        return PageMetadata(title=title, text=text[:30000])

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()
