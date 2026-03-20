from __future__ import annotations

from pathlib import Path

import pytest

from enricher.cache import EnrichmentCache
from enricher.models import CandidateMatch, EnrichmentDecision


def _make_decision(track_id: str = "1") -> EnrichmentDecision:
    return EnrichmentDecision(
        track_id=track_id,
        artist="Artist",
        title="Track",
        status="enriched",
        match=CandidateMatch(
            source="musicbrainz",
            source_id="x",
            artist="Artist",
            title="Track",
            label="Defected",
            year="2021",
            confidence=0.9,
        ),
        fields_changed={"label": ("", "Defected")},
        disambiguation_used=None,
    )


def test_cache_put_and_get_returns_decision(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    decision = _make_decision()
    cache.put("Artist", "Track", [], decision)
    result = cache.get("Artist", "Track")
    assert result is not None
    _, retrieved = result
    assert retrieved.status == "enriched"


def test_cache_get_returns_none_for_missing_key(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    assert cache.get("Unknown", "Track") is None


def test_cache_entry_never_expires(tmp_path: Path) -> None:
    import json

    cache = EnrichmentCache(tmp_path / "cache.json")
    cache.put("Artist", "Track", [], _make_decision())
    cache.flush()
    # Backdate the entry far into the past — should still be returned
    data = json.loads((tmp_path / "cache.json").read_text())
    key = list(data.keys())[0]
    data[key]["looked_up_at"] = "2000-01-01T00:00:00+00:00"
    (tmp_path / "cache.json").write_text(json.dumps(data))

    fresh_cache = EnrichmentCache(tmp_path / "cache.json")
    assert fresh_cache.get("Artist", "Track") is not None


def test_cache_flush_writes_file(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = EnrichmentCache(cache_path)
    cache.put("Artist", "Track", [], _make_decision())
    cache.flush()
    assert cache_path.exists()


def test_cache_normalises_key_case_insensitive(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    decision = _make_decision()
    cache.put("DJ EXAMPLE", "SOME TRACK", [], decision)
    result = cache.get("dj example", "some track")
    assert result is not None


def test_cache_loads_existing_file_on_init(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = EnrichmentCache(cache_path)
    cache.put("Artist", "Track", [], _make_decision())
    cache.flush()

    reloaded = EnrichmentCache(cache_path)
    assert reloaded.get("Artist", "Track") is not None


@pytest.mark.parametrize("count", [49, 50, 51])
def test_cache_auto_flushes_at_interval(tmp_path: Path, count: int) -> None:
    cache_path = tmp_path / "cache.json"
    cache = EnrichmentCache(cache_path)
    for i in range(count):
        cache.put(f"Artist{i}", f"Track{i}", [], _make_decision(str(i)))
    # After 50 puts the cache should have auto-flushed
    if count >= 50:
        assert cache_path.exists()
