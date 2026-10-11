from __future__ import annotations

import re
import time

from ollama import Client

from .config import Config
from .search import SearchResult

_SYSTEM_PROMPT = (
    "You are a strict relevance classifier for a PDF document search engine. "
    "You are given a topic and metadata of a single document. "
    "Decide whether the document is clearly relevant to the topic. "
    "Reply with exactly one word: YES or NO. Never explain and never add other words."
)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class ModelUnavailable(RuntimeError):
    pass


class RelevanceChecker:
    def __init__(self, cfg: Config, logger, events) -> None:
        self.cfg = cfg
        self.logger = logger
        self.events = events
        self.client = Client(host=cfg.ollama_host, timeout=cfg.relevance_timeout)
        self.checked = 0
        self.relevant = 0
        self.errors = 0

    def ensure_model(self) -> None:
        try:
            listing = self.client.list()
        except Exception as exc:  # noqa: BLE001
            raise ModelUnavailable(f"cannot reach Ollama at {self.cfg.ollama_host}: {exc!r}") from exc

        names: set[str] = set()
        models = getattr(listing, "models", None)
        if models is None and isinstance(listing, dict):
            models = listing.get("models", [])
        for model in models or []:
            for attr in ("model", "name"):
                value = getattr(model, attr, None)
                if value is None and isinstance(model, dict):
                    value = model.get(attr)
                if value:
                    value = str(value)
                    names.add(value)
                    if ":" in value:
                        names.add(value.split(":", 1)[0])
                    else:
                        names.add(f"{value}:latest")

        if self.cfg.model not in names:
            raise ModelUnavailable(
                f"model {self.cfg.model!r} not found in Ollama; available: {sorted(names)}"
            )
        self.logger.info("Ollama model ready: %s (host=%s)", self.cfg.model, self.cfg.ollama_host)

    @staticmethod
    def _strip_thinking(text: str) -> str:
        text = _THINK_RE.sub(" ", text)
        if "</think>" in text.lower():
            idx = text.lower().rfind("</think>")
            text = text[idx + len("</think>"):]
        return text

    @classmethod
    def _parse_verdict(cls, text: str) -> bool | None:
        cleaned = cls._strip_thinking(text or "").strip().lower()
        if not cleaned:
            return None
        if re.search(r"\byes\b", cleaned):
            return True
        if re.search(r"\bno\b", cleaned):
            return False
        return None

    def _ask(self, theme: str, result: SearchResult, strict: bool) -> tuple[bool | None, str]:
        if strict:
            user = (
                f"Topic: {theme}\n"
                f"Document title: {result.title}\n"
                f"Document URL: {result.url}\n"
                f"Document snippet: {result.snippet}\n\n"
                "Is this document clearly relevant to the topic? "
                "Answer with exactly one word: YES or NO."
            )
        else:
            user = (
                f"Topic: {theme}\n"
                f"Document title: {result.title}\n"
                f"Document URL: {result.url}\n"
                f"Document snippet: {result.snippet}\n\n"
                "Is this document relevant to the topic? Answer YES or NO."
            )

        response = self.client.chat(
            model=self.cfg.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            think=False,
            options={"temperature": 0, "num_predict": 8, "top_p": 1.0, "seed": 42},
        )
        content = ""
        message = getattr(response, "message", None)
        if message is not None:
            content = getattr(message, "content", "") or ""
        elif isinstance(response, dict):
            content = (response.get("message") or {}).get("content", "") or ""
        return self._parse_verdict(content), content

    def check(self, theme: str, result: SearchResult) -> bool:
        self.checked += 1
        attempts = max(1, self.cfg.relevance_retries)
        for attempt in range(1, attempts + 1):
            strict = attempt > 1
            started = time.perf_counter()
            try:
                verdict, raw = self._ask(theme, result, strict=strict)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                self.logger.error(
                    "relevance model error: theme=%r url=%s attempt=%d error=%r",
                    theme, result.url, attempt, exc,
                )
                self.events.emit("relevance_error", theme=theme, url=result.url, attempt=attempt, error=repr(exc))
                continue

            latency = time.perf_counter() - started
            if verdict is not None:
                if verdict:
                    self.relevant += 1
                self.logger.info(
                    "relevance: theme=%r verdict=%s latency=%.2fs url=%s",
                    theme, "RELEVANT" if verdict else "IRRELEVANT", latency, result.url,
                )
                self.events.emit(
                    "relevance",
                    theme=theme,
                    url=result.url,
                    title=result.title,
                    verdict=verdict,
                    attempt=attempt,
                    latency_s=round(latency, 3),
                    raw=raw.strip()[:200],
                )
                return verdict

            self.logger.warning(
                "relevance unparsable: theme=%r url=%s attempt=%d raw=%r",
                theme, result.url, attempt, raw.strip()[:120],
            )
            self.events.emit(
                "relevance_unparsable",
                theme=theme,
                url=result.url,
                attempt=attempt,
                raw=raw.strip()[:200],
            )

        fallback = self.cfg.model_error_relevant
        self.logger.warning(
            "relevance fallback applied: theme=%r url=%s fallback_relevant=%s",
            theme, result.url, fallback,
        )
        self.events.emit("relevance_fallback", theme=theme, url=result.url, verdict=fallback)
        if fallback:
            self.relevant += 1
        return fallback
