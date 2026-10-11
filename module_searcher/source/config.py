from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, fields
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def slugify(value: str, max_len: int = 60) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^\w\s\-]+", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return (value or "theme")[:max_len]


@dataclass
class Config:
    themes_file: Path = BASE_DIR / "themes.txt"
    download_dir: Path = BASE_DIR / "downloads"
    log_dir: Path = BASE_DIR / "logs"

    model: str = "x-gguf"
    ollama_host: str = "http://127.0.0.1:11434"
    region: str = "ru-ru"
    safesearch: str = "moderate"

    batch_min: int = 15
    batch_max: int = 35
    batch_delay_min: float = 3.0
    batch_delay_max: float = 5.0
    max_batches_per_theme: int = 20
    max_results_per_theme: int = 90
    max_search_pages: int = 8
    search_timeout: float = 10.0

    request_timeout: float = 40.0
    download_retries: int = 3
    max_file_mb: float = 120.0
    download_delay_min: float = 0.0
    download_delay_max: float = 0.0

    relevance_retries: int = 2
    relevance_timeout: float = 120.0
    model_error_relevant: bool = False

    scan_sample_pages: int = 5
    scan_min_text_per_page: int = 80
    scan_min_image_coverage: float = 0.55
    keep_scans: bool = False

    dry_run: bool = False
    log_level: str = "INFO"
    seed: int | None = None

    def ensure_dirs(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = str(value) if isinstance(value, Path) else value
        return out


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        prog="module_searcher",
        description="Search PDF files via DuckDuckGo, filter by relevance with a local Ollama model, download and reject scans.",
    )
    parser.add_argument("--themes-file", type=Path, default=Config.themes_file)
    parser.add_argument("--download-dir", type=Path, default=Config.download_dir)
    parser.add_argument("--log-dir", type=Path, default=Config.log_dir)

    parser.add_argument("--model", default=Config.model)
    parser.add_argument("--ollama-host", default=Config.ollama_host)
    parser.add_argument("--region", default=Config.region)
    parser.add_argument("--safesearch", choices=["on", "moderate", "off"], default=Config.safesearch)

    parser.add_argument("--batch-min", type=int, default=Config.batch_min)
    parser.add_argument("--batch-max", type=int, default=Config.batch_max)
    parser.add_argument("--batch-delay-min", type=float, default=Config.batch_delay_min)
    parser.add_argument("--batch-delay-max", type=float, default=Config.batch_delay_max)
    parser.add_argument("--max-batches-per-theme", type=int, default=Config.max_batches_per_theme)
    parser.add_argument("--max-results-per-theme", type=int, default=Config.max_results_per_theme)
    parser.add_argument("--max-search-pages", type=int, default=Config.max_search_pages)
    parser.add_argument("--search-timeout", type=float, default=Config.search_timeout)

    parser.add_argument("--request-timeout", type=float, default=Config.request_timeout)
    parser.add_argument("--download-retries", type=int, default=Config.download_retries)
    parser.add_argument("--max-file-mb", type=float, default=Config.max_file_mb)
    parser.add_argument("--download-delay-min", type=float, default=Config.download_delay_min)
    parser.add_argument("--download-delay-max", type=float, default=Config.download_delay_max)

    parser.add_argument("--relevance-retries", type=int, default=Config.relevance_retries)
    parser.add_argument("--relevance-timeout", type=float, default=Config.relevance_timeout)
    parser.add_argument("--model-error-relevant", action=argparse.BooleanOptionalAction, default=Config.model_error_relevant)

    parser.add_argument("--scan-sample-pages", type=int, default=Config.scan_sample_pages)
    parser.add_argument("--scan-min-text-per-page", type=int, default=Config.scan_min_text_per_page)
    parser.add_argument("--scan-min-image-coverage", type=float, default=Config.scan_min_image_coverage)
    parser.add_argument("--keep-scans", action=argparse.BooleanOptionalAction, default=Config.keep_scans)

    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=Config.dry_run)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default=Config.log_level)
    parser.add_argument("--seed", type=int, default=Config.seed)

    args = parser.parse_args(argv)

    if args.batch_min < 1 or args.batch_max < args.batch_min:
        parser.error("--batch-min must be >= 1 and --batch-max >= --batch-min")
    if args.batch_delay_min < 0 or args.batch_delay_max < args.batch_delay_min:
        parser.error("--batch-delay-min must be >= 0 and --batch-delay-max >= --batch-delay-min")

    return Config(
        themes_file=args.themes_file,
        download_dir=args.download_dir,
        log_dir=args.log_dir,
        model=args.model,
        ollama_host=args.ollama_host,
        region=args.region,
        safesearch=args.safesearch,
        batch_min=args.batch_min,
        batch_max=args.batch_max,
        batch_delay_min=args.batch_delay_min,
        batch_delay_max=args.batch_delay_max,
        max_batches_per_theme=args.max_batches_per_theme,
        max_results_per_theme=args.max_results_per_theme,
        max_search_pages=args.max_search_pages,
        search_timeout=args.search_timeout,
        request_timeout=args.request_timeout,
        download_retries=args.download_retries,
        max_file_mb=args.max_file_mb,
        download_delay_min=args.download_delay_min,
        download_delay_max=args.download_delay_max,
        relevance_retries=args.relevance_retries,
        relevance_timeout=args.relevance_timeout,
        model_error_relevant=args.model_error_relevant,
        scan_sample_pages=args.scan_sample_pages,
        scan_min_text_per_page=args.scan_min_text_per_page,
        scan_min_image_coverage=args.scan_min_image_coverage,
        keep_scans=args.keep_scans,
        dry_run=args.dry_run,
        log_level=args.log_level,
        seed=args.seed,
    )
