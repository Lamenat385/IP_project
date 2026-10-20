# AGENTS.md

## Repo layout

Single git repo at the root; each module is self-contained with its own
`requirements.txt`, `source/`, and (locally) `.venv/`. There is **no** root
virtualenv, test suite, linter, formatter, or CI. Do not invent commands.

- `module_searcher/` — implemented: DuckDuckGo search + Ollama relevance filter +
  PDF download/scan detection. Entrypoint `run.py`.
- `module_pdf2md/` — implemented: PDF → one cleaned `.md`/`.txt` (`docconvert`).
- `module_database_maker/`, `module_learning/`, `module_tools/`, `module_web/`,
  `module_bench/` — stubs (only `source/__init__.py` + empty `requirements.txt`).

Never commit `*.pdf`, `*.gguf`, `.venv/`, `.docling-cache/`, or generated
`converted.md`/`converted.txt` (see `.gitignore`).

## module_pdf2md

Run everything from inside `module_pdf2md/` (default input folder is `docs/`,
relative to cwd):

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .                # editable; package lives under source/
.\docconvert.cmd --version      # wrapper: uses module .venv, else system python
.\docconvert.cmd docs --pages 1-30 -o sample.md   # quick single-PDF check
```

- **Layout gotcha:** the module README's "Структура проекта" tree wrongly shows
  `docs_convertor/src/`. The real package is `source/docconvert/`
  (`pyproject.toml` uses `packages.find where = ["source"]`). Trust the config.
- Entrypoint `docconvert = docconvert.cli:main`; `_parse_pages` handles `1-20`
  or `5`.
- Cleaning is **two-stage**: `clean_document()` mutates the `DoclingDocument`
  (furniture/TOC/repeated edge lines, uses bbox coords), then `clean_text()`
  post-processes the exported markdown/text. `cli.py` orchestrates and assembles
  the single output.
- Math: formula recognition (`do_formula_enrichment`, `CodeFormulaV2`) is **on by
  default** (`--no-formulas` disables) and is the main runtime cost; it exports
  display formulas as `$$…$$` LaTeX. `cleanup.py` also has `_repair_symbols()`
  (U+FFFD glyphs → `≠`/`′`/`□`, `Ð`/`Ç`/`È` mojibake, HTML entities) and
  `_sanitize_formulas()` (strips decoder loops, `\intertext`, transliterated
  Cyrillic from formula blocks), toggleable via `--no-fix-symbols` /
  `--no-sanitize-formulas`. These are text-level (`clean_text`), not cache-keyed.
- `--cache-dir` JSON cache is keyed on file size + mtime + full contents +
  `repr(options)`, so changing any convert option invalidates it (useful when
  iterating on cleanup without re-running docling).
- Defaults/behavior: md output inserts `# <stem>` + `<!-- source: ... -->` per
  doc and `\n\n---\n\n` separators; `--split` writes per-doc files next to `-o`.
- Exit codes: `2` = no documents found, `1` = nothing converted. A failure on one
  document logs and continues (`cli.py:239`); use `-v` for tracebacks.
- EasyOCR is an optional extra (`pip install -e ".[easyocr]"`); GPU needs a CUDA
  torch wheel, else `--device cuda` warns and falls back to CPU.

## module_searcher

Requires a local Ollama model before running:
`ollama create x-gguf -f Modelfile`. `themes.txt` is currently empty, so a run
does nothing until it is filled (one topic per line). See root `README.md` for
the full flag table.
