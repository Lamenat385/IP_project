from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config, slugify
from .search import SearchResult

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_PDF_MAGIC = b"%PDF"


class _NotPdfError(Exception):
    pass


@dataclass
class DownloadResult:
    url: str
    path: Path | None
    ok: bool
    reason: str
    size: int = 0
    content_type: str = ""
    duration_s: float = 0.0


class Downloader:
    def __init__(self, cfg: Config, logger, events) -> None:
        self.cfg = cfg
        self.logger = logger
        self.events = events
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/pdf,*/*"})
        self.downloaded = 0
        self.failed = 0
        self.skipped = 0

    def _target_path(self, theme: str, url: str, index: int) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        name = f"{slugify(theme)}__{index:04d}__{digest}.pdf"
        return self.cfg.download_dir / name

    def download(self, theme: str, result: SearchResult, index: int) -> DownloadResult:
        dest = self._target_path(theme, result.url, index)
        if dest.exists() and dest.stat().st_size > 0:
            self.skipped += 1
            self.logger.info("download skipped (exists): %s", dest.name)
            self.events.emit("download_skip", theme=theme, url=result.url, path=str(dest))
            return DownloadResult(result.url, dest, True, "exists")

        part = dest.with_suffix(".pdf.part")
        max_bytes = int(self.cfg.max_file_mb * 1024 * 1024)
        last_error = "unknown"

        for attempt in range(1, max(1, self.cfg.download_retries) + 1):
            started = time.perf_counter()
            try:
                with self.session.get(
                    result.url, stream=True, timeout=(10.0, self.cfg.request_timeout), allow_redirects=True
                ) as response:
                    content_type = response.headers.get("Content-Type", "")
                    if response.status_code != 200:
                        last_error = f"http_status_{response.status_code}"
                        self.logger.warning(
                            "download attempt %d: status=%s url=%s", attempt, response.status_code, result.url
                        )
                        continue

                    size = 0
                    first_chunk = True
                    with part.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=65536):
                            if not chunk:
                                continue
                            if first_chunk:
                                first_chunk = False
                                if not chunk.startswith(_PDF_MAGIC):
                                    last_error = "not_a_pdf"
                                    self.logger.warning(
                                        "download attempt %d: content is not a PDF (magic=%r) url=%s",
                                        attempt, chunk[:8], result.url,
                                    )
                                    self.events.emit(
                                        "download_reject", theme=theme, url=result.url, reason="not_a_pdf",
                                        content_type=content_type,
                                    )
                                    raise _NotPdfError()
                            size += len(chunk)
                            if size > max_bytes:
                                last_error = "too_large"
                                raise ValueError(f"file exceeds max size {self.cfg.max_file_mb} MB")
                            handle.write(chunk)

                if size == 0:
                    last_error = "empty_file"
                    continue

                part.replace(dest)
                duration = time.perf_counter() - started
                self.downloaded += 1
                self.logger.info(
                    "downloaded: %s size=%d bytes type=%s in %.2fs url=%s",
                    dest.name, size, content_type or "unknown", duration, result.url,
                )
                self.events.emit(
                    "download_ok",
                    theme=theme,
                    url=result.url,
                    path=str(dest),
                    size=size,
                    content_type=content_type,
                    duration_s=round(duration, 3),
                )
                return DownloadResult(result.url, dest, True, "ok", size, content_type, duration)

            except _NotPdfError:
                last_error = "not_a_pdf"
                break
            except Exception as exc:  # noqa: BLE001
                last_error = repr(exc)
                self.logger.warning(
                    "download attempt %d failed: url=%s error=%r", attempt, result.url, exc
                )
            finally:
                if part.exists():
                    part.unlink(missing_ok=True)

        self.failed += 1
        self.logger.error("download failed permanently: url=%s reason=%s", result.url, last_error)
        self.events.emit("download_fail", theme=theme, url=result.url, reason=last_error)
        return DownloadResult(result.url, None, False, last_error)
