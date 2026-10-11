from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from .config import Config


@dataclass
class SearchResult:
    theme: str
    query: str
    title: str
    url: str
    snippet: str
    page: int


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def is_pdf_url(url: str) -> bool:
    if not url:
        return False
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return False
    return urlsplit(url).path.lower().endswith(".pdf")


class PdfSearch:
    def __init__(self, cfg: Config, logger, events) -> None:
        self.cfg = cfg
        self.logger = logger
        self.events = events
        self.pages_fetched = 0
        self.raw_items_seen = 0
        self.pdf_items_found = 0

    def build_query(self, theme: str) -> str:
        return f"{theme} filetype:pdf"

    def collect_batch(
        self,
        theme: str,
        batch_size: int,
        seen_urls: set[str],
        start_page: int,
    ) -> tuple[list[SearchResult], int]:
        query = self.build_query(theme)
        collected: list[SearchResult] = []
        page = start_page
        pages_here = 0

        while len(collected) < batch_size and pages_here < self.cfg.max_search_pages:
            self.logger.debug(
                "search: theme=%r page=%d need=%d query=%r",
                theme, page, batch_size - len(collected), query,
            )
            raw: list[dict] = []
            try:
                raw = DDGS(timeout=self.cfg.search_timeout).text(
                    query,
                    region=self.cfg.region,
                    safesearch=self.cfg.safesearch,
                    max_results=max(batch_size * 2, 20),
                    page=page,
                )
            except DDGSException as exc:
                self.logger.error("search failed: theme=%r page=%d error=%s", theme, page, exc)
                self.events.emit("search_error", theme=theme, query=query, page=page, error=repr(exc))
                break
            except Exception as exc:  # noqa: BLE001
                self.logger.error("search unexpected error: theme=%r page=%d error=%r", theme, page, exc)
                self.events.emit("search_error", theme=theme, query=query, page=page, error=repr(exc))
                break

            self.pages_fetched += 1
            self.raw_items_seen += len(raw)
            self.logger.info(
                "search page done: theme=%r page=%d raw_results=%d",
                theme, page, len(raw),
            )
            self.events.emit("search_page", theme=theme, query=query, page=page, raw_results=len(raw))

            for item in raw:
                url = item.get("href") or item.get("url") or ""
                if not is_pdf_url(url):
                    continue
                key = normalize_url(url)
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                self.pdf_items_found += 1
                collected.append(
                    SearchResult(
                        theme=theme,
                        query=query,
                        title=(item.get("title") or "").strip(),
                        url=key,
                        snippet=(item.get("body") or "").strip(),
                        page=page,
                    )
                )
                if len(collected) >= batch_size:
                    break

            if not raw:
                self.logger.warning("search returned no results: theme=%r page=%d, stopping pagination", theme, page)
                break

            page += 1
            pages_here += 1

        return collected[:batch_size], page
