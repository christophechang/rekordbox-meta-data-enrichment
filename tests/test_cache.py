from __future__ import annotations

import json
from pathlib import Path

from enricher.cache import EnrichmentCache
from enricher.models import CandidateMatch


def _cand(label: str = "Hotflush") -> CandidateMatch:
    return CandidateMatch(source="discogs", source_id="1", artist="A", title="T", label=label)


def test_cache_stores_and_returns_candidates(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "c.json")
    cache.put("A", "T", [_cand()])
    got = cache.get("A", "T")
    assert got is not None and got[0].label == "Hotflush"


def test_cache_ignores_empty_candidate_lists(tmp_path: Path) -> None:
    # no_match retry semantics live here: an empty result is never persisted
    cache = EnrichmentCache(tmp_path / "c.json")
    cache.put("A", "T", [])
    assert cache.get("A", "T") is None


def test_cache_flush_is_atomic_and_versioned(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    cache = EnrichmentCache(path)
    cache.put("A", "T", [_cand()])
    cache.flush()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2 and "entries" in raw
    assert not path.with_suffix(".tmp").exists()


def test_cache_migrates_v1_salvaging_candidates_dropping_decisions(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    v1 = {
        "a — t": {
            "looked_up_at": "2026-01-01T00:00:00+00:00",
            "candidates": [_cand().model_dump()],
            "decision": {"track_id": "99", "artist": "A", "title": "T", "status": "enriched"},
        },
        "b — u": {
            "looked_up_at": "2026-01-01T00:00:00+00:00",
            "candidates": [],
            "decision": {"track_id": "1", "artist": "B", "title": "U", "status": "skipped_already_complete"},
        },
    }
    path.write_text(json.dumps(v1), encoding="utf-8")
    cache = EnrichmentCache(path)
    assert cache.get("A", "T") is not None  # candidates salvaged
    assert cache.get("B", "U") is None  # empty-candidates entry dropped


def test_cache_recovers_from_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    cache = EnrichmentCache(path)  # must not raise
    assert cache.get("A", "T") is None
    corrupt_backups = list(tmp_path.glob("c.corrupt-*"))
    assert len(corrupt_backups) == 1
