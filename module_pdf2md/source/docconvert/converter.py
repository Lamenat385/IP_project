"""Thin wrapper around docling with caching and OCR options."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

logger = logging.getLogger("docconvert")

SUPPORTED_SUFFIXES = {".pdf"}


@dataclass
class ConvertOptions:
    ocr: str = "auto"  # auto | always | never
    ocr_engine: str = "auto"  # auto | rapidocr | easyocr | tesseract
    ocr_lang: tuple[str, ...] = ()
    tables: bool = True
    cache_dir: Path | None = None
    images_scale: float = 1.0
    page_range: tuple[int, int] | None = None
    device: str = "auto"  # auto | cpu | cuda


def torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # pragma: no cover - torch missing
        return False


def resolve_device(device: str) -> str:
    """Resolve 'auto' to 'cuda' when a CUDA device is usable, else 'cpu'."""

    if device == "auto":
        return "cuda" if torch_cuda_available() else "cpu"
    if device == "cuda" and not torch_cuda_available():
        logger.warning(
            "CUDA requested but not available (CPU-only torch build or no GPU); "
            "falling back to CPU. Reinstall torch with a CUDA wheel to enable GPU."
        )
        return "cpu"
    return device


def _make_ocr_options(options: ConvertOptions, use_gpu: bool):
    """Build OCR options for an explicitly requested engine, if any."""
    engine = options.ocr_engine
    if engine == "auto":
        return None

    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        RapidOcrOptions,
        TesseractCliOcrOptions,
    )

    if engine == "easyocr":
        ocr = EasyOcrOptions(
            lang=list(options.ocr_lang) or ["en"],
            use_gpu=use_gpu,
            download_enabled=True,
        )
    elif engine == "rapidocr":
        ocr = RapidOcrOptions(lang=list(options.ocr_lang) or ["ch"])
    elif engine == "tesseract":
        ocr = TesseractCliOcrOptions(lang=list(options.ocr_lang) or ["eng"])
    else:
        raise ValueError(f"Unknown OCR engine: {engine!r}")

    if options.ocr == "always":
        try:
            ocr.mode = OcrMode.FULL_PAGE
        except Exception:  # pragma: no cover - engine specific
            pass
    return ocr


def build_converter(options: ConvertOptions) -> DocumentConverter:
    from docling.datamodel.accelerator_options import AcceleratorDevice

    device = resolve_device(options.device)
    pipeline = PdfPipelineOptions()
    pipeline.do_table_structure = options.tables
    pipeline.images_scale = options.images_scale
    try:
        pipeline.accelerator_options.device = AcceleratorDevice(device)
    except Exception as exc:  # pragma: no cover - older docling
        logger.warning("Could not set accelerator device to %s: %s", device, exc)

    if options.ocr == "never":
        pipeline.do_ocr = False
    elif options.ocr in ("auto", "always"):
        pipeline.do_ocr = True
        if options.ocr == "always" and options.ocr_engine == "auto":
            try:
                pipeline.ocr_options.mode = OcrMode.FULL_PAGE
            except Exception:  # pragma: no cover - engine specific
                pass
    else:
        raise ValueError(f"Unknown OCR mode: {options.ocr!r}")

    explicit = _make_ocr_options(options, use_gpu=(device == "cuda"))
    if explicit is not None:
        pipeline.ocr_options = explicit
        if options.ocr == "never":
            pipeline.do_ocr = False
    elif options.ocr_lang:
        try:
            pipeline.ocr_options.lang = list(options.ocr_lang)
        except Exception as exc:  # pragma: no cover - engine specific
            logger.warning("Could not set OCR languages %s: %s", options.ocr_lang, exc)

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}
    )


def _cache_key(path: Path, options: ConvertOptions) -> str:
    h = hashlib.sha1()
    stat = path.stat()
    h.update(str(stat.st_size).encode())
    h.update(str(int(stat.st_mtime)).encode())
    h.update(repr(options).encode())
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def convert_file(
    path: Path,
    converter: DocumentConverter,
    options: ConvertOptions,
):
    """Convert a single document, using the cache when configured."""

    cache_file: Path | None = None
    if options.cache_dir is not None:
        options.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = options.cache_dir / f"{path.stem}.{_cache_key(path, options)}.json"
        if cache_file.exists():
            logger.info("Using cache for %s", path.name)
            from docling_core.types.doc import DoclingDocument

            return DoclingDocument.load_from_json(cache_file)

    if options.page_range is not None:
        result = converter.convert(path, page_range=options.page_range)
    else:
        result = converter.convert(path)
    doc = result.document

    if cache_file is not None:
        try:
            from docling_core.types.doc import ImageRefMode

            doc.save_as_json(cache_file, image_mode=ImageRefMode.PLACEHOLDER)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not write cache for %s: %s", path.name, exc)

    return doc


def find_documents(inputs: Iterable[Path], recursive: bool = False) -> list[Path]:
    """Expand files/folders into a sorted list of supported documents."""

    found: list[Path] = []
    seen: set[Path] = set()

    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            for candidate in sorted(path.glob(pattern)):
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES:
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        found.append(candidate)
        elif path.is_file():
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    found.append(path)
        else:
            logger.warning("Skipping missing path: %s", path)

    return found
