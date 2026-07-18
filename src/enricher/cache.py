from __future__ import annotations

import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from enricher.models import CandidateMatch


class CacheProtocol(Protocol):
    def get(self, artist: str, title: str) -> list[CandidateMatch] | None: ...
    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None: ...
    def flush(self) -> None: ...


_SAVE_INTERVAL = 50  # write to disk every N puts


class NullCache:
    """Drop-in replacement for EnrichmentCache that never reads or writes anything."""

    def get(self, artist: str, title: str) -> list[CandidateMatch] | None:
        return None

    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None:
        pass

    def flush(self) -> None:
        pass


def _normalise_key(artist: str, title: str) -> str:
    raw = f"{artist} — {title}".lower()
    return unicodedata.normalize("NFC", raw)


class EnrichmentCache:
    """Caches raw lookup candidates only — never decisions.

    Decisions are recomputed every run from (current track state, candidates, config),
    so completeness, flags, and track ids are always fresh. Empty candidate lists are
    never stored (no_match retries on every run — load-bearing, see AGENTS.md).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, Any] = {}
        self._dirty_count = 0
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
            backup = path.with_suffix(f".corrupt-{stamp}")
            path.rename(backup)
            print(f"WARNING: cache file corrupt, moved to {backup}; starting fresh", file=sys.stderr)
            return
        if isinstance(raw, dict) and raw.get("version") == 2:
            entries = raw.get("entries", {})
            self._entries = entries if isinstance(entries, dict) else {}
        elif isinstance(raw, dict):
            # v1 migration: salvage candidates, discard stored decisions
            for key, entry in raw.items():
                if isinstance(entry, dict) and entry.get("candidates"):
                    self._entries[key] = {
                        "looked_up_at": entry.get("looked_up_at", ""),
                        "candidates": entry["candidates"],
                    }
            self._dirty_count = 1  # persist the migrated shape on next flush

    def get(self, artist: str, title: str) -> list[CandidateMatch] | None:
        entry = self._entries.get(_normalise_key(artist, title))
        if entry is None:
            return None
        return [CandidateMatch(**c) for c in entry["candidates"]]

    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None:
        if not candidates:
            return
        self._entries[_normalise_key(artist, title)] = {
            "looked_up_at": datetime.now(tz=timezone.utc).isoformat(),
            "candidates": [c.model_dump() for c in candidates],
        }
        self._dirty_count += 1
        if self._dirty_count >= _SAVE_INTERVAL:
            self.flush()

    def flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump({"version": 2, "entries": self._entries}, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)
        self._dirty_count = 0
