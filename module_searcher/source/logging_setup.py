from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JsonlEventLogger:
    def __init__(self, path: Path) -> None:
        self._path = path

    def emit(self, event: str, **fields: object) -> None:
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        record.update(fields)
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass


def setup_logging(log_dir: Path, run_id: str, level: str = "INFO") -> tuple[logging.Logger, JsonlEventLogger]:
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("searcher")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_dir / f"run_{run_id}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(getattr(logging, level.upper(), logging.INFO))

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    events = JsonlEventLogger(log_dir / f"events_{run_id}.jsonl")
    return logger, events
