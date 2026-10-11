"""Cleanup routines for docling documents and their textual exports.

The goal is to remove the "garbage" that PDF extraction produces: running
headers/footers, page numbers, hyphenation artifacts, empty spacing, OCR
punctuation noise and trailing bibliography sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from docling_core.types.doc import DocItemLabel

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class CleanupOptions:
    """Controls which cleanup rules are applied."""

    # document level
    drop_headers_footers: bool = True
    drop_repeated_lines: bool = True
    drop_toc: bool = False

    # text level
    dehyphenate: bool = True
    drop_page_numbers: bool = True
    drop_noise_lines: bool = True
    drop_references: bool = True
    collapse_whitespace: bool = True

    # tuning
    edge_ratio: float = 0.14
    repeat_min_pages: int = 3
    repeat_min_ratio: float = 0.5
    max_header_len: int = 120


@dataclass
class CleanupStats:
    """Small report about what was removed."""

    headers_footers: int = 0
    repeated_lines: int = 0
    toc_items: int = 0
    page_numbers: int = 0
    noise_lines: int = 0
    dehyphenated: int = 0
    references_cut: bool = False
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.headers_footers:
            parts.append(f"{self.headers_footers} header/footer")
        if self.repeated_lines:
            parts.append(f"{self.repeated_lines} repeated")
        if self.toc_items:
            parts.append(f"{self.toc_items} toc")
        if self.page_numbers:
            parts.append(f"{self.page_numbers} page-number")
        if self.noise_lines:
            parts.append(f"{self.noise_lines} noise")
        if self.dehyphenated:
            parts.append(f"{self.dehyphenated} hyphen joins")
        if self.references_cut:
            parts.append("references cut")
        return ", ".join(parts) if parts else "nothing removed"


# ---------------------------------------------------------------------------
# Document level cleanup
# ---------------------------------------------------------------------------

_FURNITURE_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
_REPEATABLE_LABELS = {
    DocItemLabel.PARAGRAPH,
    DocItemLabel.TEXT,
    DocItemLabel.LIST_ITEM,
    DocItemLabel.FOOTNOTE,
    DocItemLabel.CAPTION,
}
_NUMBER_RE = re.compile(r"\d+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


def _normalize_repeat(text: str) -> str:
    text = text.lower()
    text = _NUMBER_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _item_position(doc: Any, item: Any) -> tuple[int | None, float | None, float | None]:
    """Return (page_no, distance_from_top, distance_from_bottom) for an item."""
    prov = getattr(item, "prov", None)
    if not prov:
        return None, None, None
    p = prov[0]
    page_no = getattr(p, "page_no", None)
    bbox = getattr(p, "bbox", None)
    page = doc.pages.get(page_no) if page_no is not None else None
    if page is None or bbox is None:
        return page_no, None, None
    try:
        height = page.size.height
        origin = getattr(getattr(bbox, "coord_origin", ""), "name", str(getattr(bbox, "coord_origin", "")))
        if origin.upper().startswith("BOTTOM"):
            top = height - bbox.t
            bottom = height - bbox.b
        else:
            top = bbox.t
            bottom = bbox.b
        return page_no, top, bottom
    except Exception:  # pragma: no cover - defensive
        return page_no, None, None


def _collect_furniture(doc: Any) -> list[Any]:
    items: list[Any] = []
    try:
        for item, _level in doc.iterate_items(doc.furniture):
            if getattr(item, "label", None) in _FURNITURE_LABELS or getattr(item, "text", None):
                items.append(item)
    except Exception:  # pragma: no cover - defensive
        pass
    return items


def clean_document(doc: Any, options: CleanupOptions) -> CleanupStats:
    """Remove furniture and repeated running lines from a DoclingDocument.

    The document is mutated in place so a subsequent ``export_to_markdown`` /
    ``export_to_text`` reflects the removals.
    """

    stats = CleanupStats()
    to_delete: list[Any] = []

    # Page headers/footers live in the document's "furniture" layer, which is
    # already excluded from the markdown/text body export. We still count them
    # and detach the group so they can never leak into the output.
    if options.drop_headers_footers:
        furniture = _collect_furniture(doc)
        stats.headers_footers = len(furniture)
        if furniture:
            try:
                doc.furniture.children = []
            except Exception:  # pragma: no cover - defensive
                pass

    if options.drop_toc:
        for item, _level in doc.iterate_items():
            if getattr(item, "label", None) == DocItemLabel.DOCUMENT_INDEX:
                to_delete.append(item)

    if options.drop_repeated_lines:
        to_delete.extend(_find_repeated_lines(doc, options))

    # De-duplicate by id while preserving order.
    seen: set[int] = set()
    unique: list[Any] = []
    for item in to_delete:
        key = id(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    if unique:
        stats.toc_items = sum(
            1 for i in unique if getattr(i, "label", None) == DocItemLabel.DOCUMENT_INDEX
        )
        stats.repeated_lines = len(unique) - stats.toc_items
        try:
            doc.delete_items(node_items=unique)
        except Exception as exc:  # pragma: no cover - defensive
            stats.details.append(f"delete_items failed: {exc}")

    return stats


def _find_repeated_lines(doc: Any, options: CleanupOptions) -> list[Any]:
    """Find short items that repeat on many pages near the page edges."""

    num_pages = 0
    try:
        num_pages = int(doc.num_pages())
    except Exception:  # pragma: no cover - defensive
        pass
    if num_pages < 4:
        return []

    threshold = max(options.repeat_min_pages, int(options.repeat_min_ratio * num_pages))

    pages_by_norm: dict[str, set[int]] = {}
    first_item: dict[str, Any] = {}

    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        text = getattr(item, "text", None)
        if label not in _REPEATABLE_LABELS or not text:
            continue
        if len(text) > options.max_header_len:
            continue
        page_no, top, bottom = _item_position(doc, item)
        if page_no is None or top is None or bottom is None:
            continue
        page = doc.pages.get(page_no)
        height = page.size.height if page is not None else None
        if not height:
            continue
        near_edge = top <= options.edge_ratio * height or bottom >= (1.0 - options.edge_ratio) * height
        if not near_edge:
            continue
        norm = _normalize_repeat(text)
        if len(norm) < 2:
            continue
        pages_by_norm.setdefault(norm, set()).add(page_no)
        first_item.setdefault(norm, item)

    repeats: list[Any] = []
    for norm, pages in pages_by_norm.items():
        if len(pages) >= threshold:
            repeats.append(first_item[norm])
    return repeats


# ---------------------------------------------------------------------------
# Text level cleanup
# ---------------------------------------------------------------------------

_PLACEHOLDER_COMMENT_RE = re.compile(
    r"<!--\s*(?:formula-not-decoded|image|figure|chart)\s*-->", re.IGNORECASE
)
_DEHYPHEN_RE = re.compile(
    r"([A-Za-z\u0400-\u04FF])[\-\u2010\u2011\u00ad]\n[ \t]*([a-z\u0430-\u044f\u0451])",
    re.UNICODE,
)
_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:[-\u2013\u2014\u2022\u00b7.~]+\s*)?\d{1,4}"
    r"(?:\s*[-\u2013\u2014\u2022\u00b7.~]+)?\s*$"
)
_ROMAN_PAGE_RE = re.compile(r"^\s*[-\u2013\u2014]?\s*[ivxlcdm]{2,7}\s*[-\u2013\u2014]?\s*$", re.IGNORECASE)
_RULE_RE = re.compile(r"^\s*(?:[-_=*]\s*){3,}$")
_NOISE_LINE_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)
_FENCE_RE = re.compile(r"^\s*```")

_REFERENCES_RE = re.compile(
    r"^\s*#{0,6}\s*"
    r"(references|bibliography|"
    r"\u043b\u0438\u0442\u0435\u0440\u0430\u0442\u0443\u0440\u0430|"
    r"\u0441\u043f\u0438\u0441\u043e\u043a\s+\u043b\u0438\u0442\u0435\u0440\u0430\u0442\u0443\u0440\u044b|"
    r"\u0431\u0438\u0431\u043b\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u044f|"
    r"\u0431\u0438\u0431\u043b\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u0447\u0435\u0441\u043a\u0438\u0439\s+\u0441\u043f\u0438\u0441\u043e\u043a|"
    r"\u0441\u043f\u0438\u0441\u043e\u043a\s+\u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u043d\u044b\u0445\s+\u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u043e\u0432)"
    r"\s*:?\s*$",
    re.IGNORECASE | re.UNICODE,
)


def clean_text(text: str, options: CleanupOptions, stats: CleanupStats | None = None) -> str:
    """Clean a markdown / plain-text export produced by docling."""

    stats = stats if stats is not None else CleanupStats()

    if not text:
        return text

    text = _PLACEHOLDER_COMMENT_RE.sub("", text)

    if options.dehyphenate:
        text, count = _DEHYPHEN_RE.subn(r"\1\2", text)
        stats.dehyphenated += count

    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line.rstrip())
            continue
        if in_fence:
            out.append(line.rstrip())
            continue

        stripped = line.rstrip()

        if not stripped.strip():
            out.append("")
            continue

        is_table = "|" in stripped

        if _RULE_RE.match(stripped) and not is_table:
            continue
        if options.drop_page_numbers and not is_table and (
            _PAGE_NUMBER_RE.match(stripped) or _ROMAN_PAGE_RE.match(stripped)
        ):
            stats.page_numbers += 1
            continue
        if options.drop_noise_lines and not is_table and _NOISE_LINE_RE.match(stripped):
            stats.noise_lines += 1
            continue

        if options.collapse_whitespace and not is_table:
            stripped = re.sub(r"[ \t]{2,}", " ", stripped)
        out.append(stripped)

    text = "\n".join(out)

    if options.drop_references:
        lines = text.split("\n")
        for idx, line in enumerate(lines):
            # Ignore lines inside fenced code blocks is overkill here.
            if _REFERENCES_RE.match(line):
                text = "\n".join(lines[:idx])
                stats.references_cut = True
                break

    if options.collapse_whitespace:
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)

    return text.strip() + "\n"
