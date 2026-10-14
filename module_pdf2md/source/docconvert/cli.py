"""Command line interface for docconvert."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .cleanup import CleanupOptions, clean_document, clean_text
from .converter import ConvertOptions, build_converter, convert_file, find_documents

logger = logging.getLogger("docconvert")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docconvert",
        description=(
            "Convert PDF textbooks, papers and technical documentation into a single "
            "cleaned text/markdown file using docling."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="PDF files and/or folders. Defaults to the 'docs' folder when omitted.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file (default: converted.md / converted.txt depending on --format).",
    )
    parser.add_argument(
        "--format",
        choices=("md", "txt"),
        default="md",
        help="Output format (default: md).",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Search folders recursively.",
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="Also write one cleaned file per source document next to the output.",
    )
    parser.add_argument(
        "--title",
        help="Title placed at the top of the combined output.",
    )

    ocr = parser.add_argument_group("OCR / extraction")
    ocr.add_argument(
        "--ocr",
        choices=("auto", "always", "never"),
        default="auto",
        help="OCR handling: 'auto' (docling default), 'always' (full-page OCR), 'never'.",
    )
    ocr.add_argument(
        "--ocr-engine",
        choices=("auto", "rapidocr", "easyocr", "tesseract"),
        default="auto",
        help=(
            "OCR engine to force (default: auto). 'easyocr' with '--ocr-lang ru' is "
            "usually best for Russian/scanned books; requires the easyocr package."
        ),
    )
    ocr.add_argument(
        "--ocr-lang",
        action="append",
        default=[],
        metavar="CODE",
        help=(
            "OCR language code (repeatable), e.g. 'ru', 'en', 'cyrillic', 'ch', 'iso:ru'. "
            "RapidOCR uses script codes such as 'cyrillic'; EasyOCR/Tesseract use ISO codes."
        ),
    )
    ocr.add_argument(
        "--no-tables",
        action="store_true",
        help="Disable table structure recognition (faster).",
    )
    ocr.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help=(
            "Compute device for layout/OCR (default: auto). Requires a CUDA-enabled "
            "torch build for 'cuda'."
        ),
    )

    math = parser.add_argument_group("math / formulas")
    math.add_argument(
        "--formulas",
        dest="formulas",
        action="store_true",
        default=True,
        help="Recognize formulas and export them as LaTeX (default: on).",
    )
    math.add_argument(
        "--no-formulas",
        dest="formulas",
        action="store_false",
        help="Disable formula recognition (faster; formulas become placeholders).",
    )
    parser.add_argument(
        "--pages",
        metavar="START-END",
        help="Only convert a page range, e.g. '1-20' or '15' (single page).",
    )
    parser.add_argument(
        "--cache-dir",
        help="Folder used to cache docling conversions as JSON (speeds up re-runs).",
    )

    clean = parser.add_argument_group("cleanup")
    clean.add_argument("--no-clean", action="store_true", help="Disable all cleanup rules.")
    clean.add_argument("--keep-headers-footers", action="store_true", help="Keep page headers/footers.")
    clean.add_argument("--keep-repeated-lines", action="store_true", help="Keep detected running lines.")
    clean.add_argument("--drop-toc", action="store_true", help="Remove the table of contents.")
    clean.add_argument("--no-dehyphenate", action="store_true", help="Do not join hyphenated line breaks.")
    clean.add_argument("--keep-page-numbers", action="store_true", help="Keep standalone page numbers.")
    clean.add_argument("--keep-noise", action="store_true", help="Keep punctuation-only noise lines.")
    clean.add_argument("--keep-references", action="store_true", help="Keep references/bibliography sections.")
    clean.add_argument("--no-collapse", action="store_true", help="Do not collapse whitespace / blank lines.")
    clean.add_argument(
        "--no-fix-symbols",
        action="store_true",
        help="Do not repair undecoded math glyphs (≠, ′, —, «») and HTML entities.",
    )
    clean.add_argument(
        "--no-sanitize-formulas",
        action="store_true",
        help="Do not clean transliteration/loops out of recognized LaTeX formulas.",
    )

    parser.add_argument("-q", "--quiet", action="store_true", help="Only log warnings and errors.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (debug) logging.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _make_cleanup_options(args: argparse.Namespace) -> CleanupOptions:
    options = CleanupOptions()
    if args.no_clean:
        return CleanupOptions(
            drop_headers_footers=False,
            drop_repeated_lines=False,
            drop_toc=False,
            dehyphenate=False,
            drop_page_numbers=False,
            drop_noise_lines=False,
            drop_references=False,
            collapse_whitespace=False,
            fix_symbols=False,
            sanitize_formulas=False,
        )
    if args.keep_headers_footers:
        options.drop_headers_footers = False
    if args.keep_repeated_lines:
        options.drop_repeated_lines = False
    if args.drop_toc:
        options.drop_toc = True
    if args.no_dehyphenate:
        options.dehyphenate = False
    if args.keep_page_numbers:
        options.drop_page_numbers = False
    if args.keep_noise:
        options.drop_noise_lines = False
    if args.keep_references:
        options.drop_references = False
    if args.no_collapse:
        options.collapse_whitespace = False
    if args.no_fix_symbols:
        options.fix_symbols = False
    if args.no_sanitize_formulas:
        options.sanitize_formulas = False
    return options


def _parse_pages(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    value = value.strip()
    if "-" in value:
        start, end = value.split("-", 1)
        return max(1, int(start)), max(1, int(end))
    page = max(1, int(value))
    return page, page


def _build_section(doc_path: Path, text: str, output_format: str) -> str:
    if output_format == "md":
        return f"# {doc_path.stem}\n\n<!-- source: {doc_path.name} -->\n\n{text.strip()}\n"
    banner = "=" * 78
    return f"{banner}\n{doc_path.name}\n{banner}\n\n{text.strip()}\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.WARNING if args.quiet else (logging.DEBUG if args.verbose else logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    raw_inputs = [Path(p) for p in (args.inputs or ["docs"])]
    documents = find_documents(raw_inputs, recursive=args.recursive)
    if not documents:
        logger.error("No supported documents found in: %s", ", ".join(str(p) for p in raw_inputs))
        return 2

    output = Path(args.output) if args.output else Path(f"converted.{args.format}")
    if output.parent and str(output.parent) not in ("", "."):
        output.parent.mkdir(parents=True, exist_ok=True)
    split_dir = output.parent if args.split else None

    convert_options = ConvertOptions(
        ocr=args.ocr,
        ocr_engine=args.ocr_engine,
        ocr_lang=tuple(args.ocr_lang),
        tables=not args.no_tables,
        formulas=args.formulas,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        page_range=_parse_pages(args.pages),
        device=args.device,
    )

    from .converter import resolve_device

    logger.info("Compute device: %s", resolve_device(args.device))
    cleanup_options = _make_cleanup_options(args)

    logger.info("Converting %d document(s) with OCR mode '%s' ...", len(documents), args.ocr)
    converter = build_converter(convert_options)

    sections: list[str] = []
    for index, doc_path in enumerate(documents, start=1):
        logger.info("[%d/%d] %s", index, len(documents), doc_path)
        try:
            doc = convert_file(doc_path, converter, convert_options)
            if args.no_clean:
                stats = None
            else:
                stats = clean_document(doc, cleanup_options)

            if args.format == "md":
                text = doc.export_to_markdown()
            else:
                text = doc.export_to_text()

            if not args.no_clean:
                text = clean_text(text, cleanup_options, stats)
            else:
                text = text.strip() + "\n"

            if stats is not None:
                logger.info("    cleanup: %s", stats.summary())

            sections.append(_build_section(doc_path, text, args.format))

            if split_dir is not None:
                per_file = split_dir / f"{doc_path.stem}.{args.format}"
                per_file.write_text(text, encoding="utf-8")
                logger.info("    wrote %s", per_file)
        except Exception as exc:  # noqa: BLE001 - report and continue
            logger.error("Failed to convert %s: %s", doc_path, exc)
            if args.verbose:
                logger.exception("Traceback for %s", doc_path)

    if not sections:
        logger.error("Nothing was converted.")
        return 1

    header = f"# {args.title}\n\n" if args.title else ""
    separator = "\n\n---\n\n" if args.format == "md" else "\n\n"
    combined = header + separator.join(sections)
    output.write_text(combined, encoding="utf-8")
    logger.info("Wrote %s (%d document(s), %d chars)", output, len(sections), len(combined))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
