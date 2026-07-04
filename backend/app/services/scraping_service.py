from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class PageMetadata:
    title: str | None


class ScrapingService:
    def fetch_metadata(self, url: str) -> PageMetadata:
        response = requests.get(
            url,
            headers={"User-Agent": "MarathonBot/0.1"},
            timeout=2,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title_tag = soup.find("title")
        title = title_tag.get_text(" ", strip=True) if title_tag else None

        return PageMetadata(title=title)
