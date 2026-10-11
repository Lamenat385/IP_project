from __future__ import annotations

import sys
from datetime import datetime

from source.config import parse_args
from source.logging_setup import setup_logging
from source.pipeline import Pipeline
from source.relevance import ModelUnavailable


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    cfg.ensure_dirs()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger, events = setup_logging(cfg.log_dir, run_id, cfg.log_level)
    logger.info("log file: %s", (cfg.log_dir / f"run_{run_id}.log").resolve())
    logger.info("event log: %s", (cfg.log_dir / f"events_{run_id}.jsonl").resolve())

    pipeline = Pipeline(cfg, logger, events, run_id)
    try:
        summary = pipeline.run()
    except ModelUnavailable as exc:
        logger.error("cannot start: %s", exc)
        events.emit("run_aborted", reason="model_unavailable", error=str(exc))
        return 2
    except FileNotFoundError as exc:
        logger.error("cannot start: %s", exc)
        events.emit("run_aborted", reason="file_not_found", error=str(exc))
        return 2
    except KeyboardInterrupt:
        logger.warning("interrupted by user")
        events.emit("run_aborted", reason="keyboard_interrupt")
        return 130

    return 0 if summary.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
