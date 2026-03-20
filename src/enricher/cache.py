from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from enricher.models import CandidateMatch, EnrichmentDecision


class CacheProtocol(Protocol):
    def get(self, artist: str, title: str) -> tuple[list[CandidateMatch], EnrichmentDecision] | None: ...
    def put(self, artist: str, title: str, candidates: list[CandidateMatch], decision: EnrichmentDecision) -> None: ...
    def flush(self) -> None: ...

_SAVE_INTERVAL = 50  # write to disk every N decisions


class NullCache:
    """Drop-in replacement for EnrichmentCache that never reads or writes anything.

    Used with --no-cache so test runs don't pollute the persistent cache.
    """

    def get(self, artist: str, title: str) -> tuple[list[CandidateMatch], EnrichmentDecision] | None:
        return None

    def put(self, artist: str, title: str, candidates: list[CandidateMatch], decision: EnrichmentDecision) -> None:
        pass

    def flush(self) -> None:
        pass


def _normalise_key(artist: str, title: str) -> str:
    raw = f"{artist} — {title}".lower()
    return unicodedata.normalize("NFC", raw)


class EnrichmentCache:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        self._dirty_count = 0
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                self._data = json.load(fh)

    def get(self, artist: str, title: str) -> tuple[list[CandidateMatch], EnrichmentDecision] | None:
        key = _normalise_key(artist, title)
        entry = self._data.get(key)
        if entry is None:
            return None
        candidates = [CandidateMatch(**c) for c in entry["candidates"]]
        decision = EnrichmentDecision(**entry["decision"])
        return candidates, decision

    def put(self, artist: str, title: str, candidates: list[CandidateMatch], decision: EnrichmentDecision) -> None:
        key = _normalise_key(artist, title)
        self._data[key] = {
            "looked_up_at": datetime.now(tz=timezone.utc).isoformat(),
            "candidates": [c.model_dump() for c in candidates],
            "decision": decision.model_dump(),
        }
        self._dirty_count += 1
        if self._dirty_count >= _SAVE_INTERVAL:
            self.flush()

    def flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)
        self._dirty_count = 0
