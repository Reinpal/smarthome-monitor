"""HTTP client for fetching ISG web pages."""

import logging
import requests

logger = logging.getLogger(__name__)


class ISGScraper:
    """Fetches HTML pages from the Stiebel Eltron ISG web interface."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        # ISG serves XHTML with utf-8
        self.session.headers.update(
            {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "de"}
        )

    def fetch_page(self, page_path: str) -> str | None:
        """Fetch a single ISG page and return its HTML content.

        Args:
            page_path: The query string path, e.g. '?s=1,1'

        Returns:
            HTML content as string, or None if the request failed.
        """
        url = f"{self.base_url}/{page_path}"
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            logger.debug("Fetched %s (%d bytes)", url, len(response.text))
            return response.text
        except requests.RequestException as e:
            logger.error("Failed to fetch %s: %s", url, e)
            return None

    def fetch_all_pages(self, pages: dict[str, str]) -> dict[str, str]:
        """Fetch multiple ISG pages.

        Args:
            pages: Dict mapping page_path -> page_name

        Returns:
            Dict mapping page_name -> HTML content (only successful fetches).
        """
        results = {}
        for page_path, page_name in pages.items():
            html = self.fetch_page(page_path)
            if html is not None:
                results[page_name] = html
        return results
