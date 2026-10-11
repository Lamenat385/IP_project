from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .config import Config


@dataclass
class ScanReport:
    path: Path
    pages: int
    sampled_pages: int
    total_chars: int
    avg_chars_per_page: float
    avg_images_per_page: float
    avg_image_coverage: float
    is_scan: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "pages": self.pages,
            "sampled_pages": self.sampled_pages,
            "total_chars": self.total_chars,
            "avg_chars_per_page": round(self.avg_chars_per_page, 2),
            "avg_images_per_page": round(self.avg_images_per_page, 2),
            "avg_image_coverage": round(self.avg_image_coverage, 3),
            "is_scan": self.is_scan,
            "reason": self.reason,
        }


def analyze_pdf(path: Path, cfg: Config) -> ScanReport:
    doc = pymupdf.open(path)
    try:
        pages = doc.page_count
        sample = min(pages, cfg.scan_sample_pages) if pages else 0

        total_chars = 0
        total_images = 0
        total_coverage = 0.0

        for index in range(sample):
            page = doc.load_page(index)
            text = page.get_text("text") or ""
            total_chars += len(text.strip())

            images = page.get_images(full=True)
            total_images += len(images)

            page_area = abs(page.rect)
            coverage = 0.0
            if page_area > 0:
                for image in images:
                    xref = image[0]
                    try:
                        rects = page.get_image_rects(xref)
                    except Exception:  # noqa: BLE001
                        rects = []
                    for rect in rects:
                        coverage += abs(rect) / page_area
            total_coverage += min(coverage, 1.0)
    finally:
        doc.close()

    if sample == 0:
        return ScanReport(path, pages, 0, 0, 0.0, 0.0, 0.0, True, "no_pages")

    avg_chars = total_chars / sample
    avg_images = total_images / sample
    avg_coverage = total_coverage / sample

    image_present = avg_images >= 1.0 or avg_coverage >= cfg.scan_min_image_coverage
    low_text = avg_chars < cfg.scan_min_text_per_page

    if total_chars == 0 and image_present:
        is_scan, reason = True, "no_text_layer_image_only"
    elif low_text and image_present:
        is_scan, reason = True, "low_text_dense_image"
    else:
        is_scan, reason = False, "has_text_layer"

    return ScanReport(
        path=path,
        pages=pages,
        sampled_pages=sample,
        total_chars=total_chars,
        avg_chars_per_page=avg_chars,
        avg_images_per_page=avg_images,
        avg_image_coverage=avg_coverage,
        is_scan=is_scan,
        reason=reason,
    )
