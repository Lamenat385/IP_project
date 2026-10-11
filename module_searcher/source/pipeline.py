from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .downloader import Downloader
from .relevance import RelevanceChecker
from .scan_detect import analyze_pdf
from .search import PdfSearch, SearchResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunSummary:
    run_id: str
    started: str = field(default_factory=_now)
    finished: str = ""
    themes: int = 0
    search_pages: int = 0
    results_seen: int = 0
    pdf_candidates: int = 0
    relevant: int = 0
    downloaded: int = 0
    download_failed: int = 0
    download_skipped: int = 0
    scans_found: int = 0
    scans_deleted: int = 0
    kept: int = 0
    errors: int = 0
    per_theme: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started": self.started,
            "finished": self.finished,
            "themes": self.themes,
            "search_pages": self.search_pages,
            "results_seen": self.results_seen,
            "pdf_candidates": self.pdf_candidates,
            "relevant": self.relevant,
            "downloaded": self.downloaded,
            "download_failed": self.download_failed,
            "download_skipped": self.download_skipped,
            "scans_found": self.scans_found,
            "scans_deleted": self.scans_deleted,
            "kept": self.kept,
            "errors": self.errors,
            "per_theme": self.per_theme,
        }


class Pipeline:
    def __init__(self, cfg: Config, logger, events, run_id: str) -> None:
        self.cfg = cfg
        self.logger = logger
        self.events = events
        self.run_id = run_id
        self.search = PdfSearch(cfg, logger, events)
        self.checker = RelevanceChecker(cfg, logger, events)
        self.downloader = Downloader(cfg, logger, events)
        self.seen_urls: set[str] = set()
        self.index = 0
        self.downloaded_paths: list[Path] = []

    def load_themes(self) -> list[str]:
        path = self.cfg.themes_file
        if not path.exists():
            raise FileNotFoundError(f"themes file not found: {path}")
        themes: list[str] = []
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line not in themes:
                themes.append(line)
        self.logger.info("loaded %d theme(s) from %s", len(themes), path)
        self.events.emit("themes_loaded", file=str(path), count=len(themes), themes=themes)
        return themes

    def run(self) -> RunSummary:
        summary = RunSummary(run_id=self.run_id)
        self.logger.info("=" * 78)
        self.logger.info("START run_id=%s dry_run=%s", self.run_id, self.cfg.dry_run)
        for key, value in self.cfg.as_dict().items():
            self.logger.debug("config %s = %r", key, value)
        self.events.emit("run_start", run_id=self.run_id, config=self.cfg.as_dict())

        try:
            themes = self.load_themes()
            summary.themes = len(themes)
            self.checker.ensure_model()

            for theme in themes:
                self.process_theme(theme, summary)

            self.verify_downloads(summary)
        finally:
            summary.search_pages = self.search.pages_fetched
            summary.results_seen = self.search.raw_items_seen
            summary.download_skipped = self.downloader.skipped
            summary.finished = _now()

        self.logger.info("-" * 78)
        self.logger.info("SUMMARY: %s", summary.as_dict())
        self.logger.info("=" * 78)
        self.events.emit("run_end", **summary.as_dict())
        return summary

    def process_theme(self, theme: str, summary: RunSummary) -> None:
        self.logger.info("-" * 78)
        self.logger.info("THEME: %s", theme)
        self.events.emit("theme_start", theme=theme)

        results_this_theme = 0
        page_cursor = 1
        batches = max(1, self.cfg.max_batches_per_theme)

        for batch_index in range(1, batches + 1):
            remaining = self.cfg.max_results_per_theme - results_this_theme
            if remaining <= 0:
                self.logger.info("theme %r reached max results (%d), stop", theme, self.cfg.max_results_per_theme)
                break

            batch_size = min(random.randint(self.cfg.batch_min, self.cfg.batch_max), remaining)
            self.logger.info(
                "theme=%r batch=%d/%d requested_size=%d page_cursor=%d",
                theme, batch_index, batches, batch_size, page_cursor,
            )
            self.events.emit("batch_start", theme=theme, batch=batch_index, size=batch_size)

            results, page_cursor = self.search.collect_batch(theme, batch_size, self.seen_urls, page_cursor)
            if not results:
                self.logger.warning("no new PDF candidates for theme=%r, stopping this theme", theme)
                self.events.emit("theme_no_results", theme=theme, batch=batch_index)
                break

            self.logger.info("theme=%r batch=%d got %d new PDF candidate(s)", theme, batch_index, len(results))

            for result in results:
                self.handle_result(theme, result, summary)

            results_this_theme += len(results)
            summary.per_theme[theme] = results_this_theme

            if batch_index >= batches or results_this_theme >= self.cfg.max_results_per_theme:
                break

            delay = random.uniform(self.cfg.batch_delay_min, self.cfg.batch_delay_max)
            self.logger.info("theme=%r sleeping %.2fs before next batch", theme, delay)
            self.events.emit("batch_delay", theme=theme, seconds=round(delay, 3))
            time.sleep(delay)

        self.events.emit("theme_end", theme=theme, candidates=results_this_theme)

    def handle_result(self, theme: str, result: SearchResult, summary: RunSummary) -> None:
        summary.pdf_candidates += 1
        self.logger.debug("candidate: theme=%r title=%r url=%s", theme, result.title, result.url)

        relevant = self.checker.check(theme, result)
        if not relevant:
            return

        summary.relevant += 1
        self.index += 1

        if self.cfg.dry_run:
            self.logger.info("[dry-run] relevant, would download: %s", result.url)
            self.events.emit("download_dry_run", theme=theme, url=result.url)
            return

        outcome = self.downloader.download(theme, result, self.index)
        if outcome.ok and outcome.path is not None:
            summary.downloaded += 1
            self.downloaded_paths.append(outcome.path)
        else:
            summary.download_failed += 1

        if self.cfg.download_delay_max > 0:
            delay = random.uniform(self.cfg.download_delay_min, self.cfg.download_delay_max)
            time.sleep(delay)

    def verify_downloads(self, summary: RunSummary) -> None:
        self.logger.info("-" * 78)
        self.logger.info("SCAN VERIFICATION: checking PDFs in %s", self.cfg.download_dir)
        self.events.emit("scan_phase_start", directory=str(self.cfg.download_dir))

        pdfs = sorted(self.cfg.download_dir.glob("*.pdf"))
        self.logger.info("found %d PDF file(s) to verify", len(pdfs))

        for path in pdfs:
            try:
                report = analyze_pdf(path, self.cfg)
            except Exception as exc:  # noqa: BLE001
                summary.errors += 1
                self.logger.error("scan analysis failed: file=%s error=%r", path.name, exc)
                self.events.emit("scan_error", path=str(path), error=repr(exc))
                continue

            self.logger.info("scan check: %s -> %s", path.name, report.as_dict())
            self.events.emit("scan_check", **report.as_dict())

            if report.is_scan:
                summary.scans_found += 1
                if self.cfg.keep_scans:
                    self.logger.warning("scan detected but kept (--keep-scans): %s", path.name)
                    self.events.emit("scan_kept", path=str(path), reason=report.reason)
                else:
                    try:
                        path.unlink()
                        summary.scans_deleted += 1
                        self.logger.warning("deleted scan: %s (reason=%s)", path.name, report.reason)
                        self.events.emit("scan_deleted", path=str(path), reason=report.reason)
                    except OSError as exc:
                        summary.errors += 1
                        self.logger.error("failed to delete scan: file=%s error=%r", path.name, exc)
                        self.events.emit("scan_delete_error", path=str(path), error=repr(exc))
            else:
                summary.kept += 1
                self.events.emit("scan_kept", path=str(path), reason=report.reason)

        self.events.emit("scan_phase_end", found=summary.scans_found, deleted=summary.scans_deleted)
