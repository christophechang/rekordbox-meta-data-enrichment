from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from enricher.cache import EnrichmentCache
from enricher.enricher import process_track
from enricher.models import CandidateMatch, TrackRecord


def _make_track(label: str = "", year: str = "") -> TrackRecord:
    return TrackRecord(
        track_id="1",
        name="Some Track",
        artist="DJ Example",
        genre="House",
        bpm=125.0,
        tonality="4A",
        label=label,
        year=year,
        duration_seconds=360,
    )


def _high_conf_candidate() -> CandidateMatch:
    return CandidateMatch(
        source="musicbrainz",
        source_id="x",
        artist="DJ Example",
        title="Some Track",
        label="Defected",
        year="2021",
        confidence=0.0,
        duration_seconds=362,
    )


@pytest.mark.asyncio
async def test_process_track_enriches_on_high_confidence(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    with patch("enricher.enricher.lookup_musicbrainz", new_callable=AsyncMock) as mock_mb:
        mock_mb.return_value = [_high_conf_candidate()]
        with patch("enricher.enricher.lookup_discogs", new_callable=AsyncMock) as mock_discogs:
            mock_discogs.return_value = []
            decision = await process_track(_make_track(), cache=cache)
    assert decision.status == "enriched"
    assert decision.fields_changed.get("label") == ("", "Defected")


@pytest.mark.asyncio
async def test_process_track_skips_already_complete(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    track = _make_track(label="Rekids", year="2020")
    with patch("enricher.enricher.lookup_musicbrainz", new_callable=AsyncMock) as mock_mb:
        mock_mb.return_value = []
        decision = await process_track(track, cache=cache)
    assert decision.status == "skipped_already_complete"
    mock_mb.assert_not_called()


@pytest.mark.asyncio
async def test_process_track_skips_on_no_candidates(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    with patch("enricher.enricher.lookup_musicbrainz", new_callable=AsyncMock) as mock_mb:
        mock_mb.return_value = []
        with patch("enricher.enricher.lookup_discogs", new_callable=AsyncMock) as mock_discogs:
            mock_discogs.return_value = []
            decision = await process_track(_make_track(), cache=cache)
    assert decision.status == "skipped_no_match"


@pytest.mark.asyncio
async def test_process_track_uses_cache_on_second_call(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    with patch("enricher.enricher.lookup_musicbrainz", new_callable=AsyncMock) as mock_mb:
        mock_mb.return_value = [_high_conf_candidate()]
        with patch("enricher.enricher.lookup_discogs", new_callable=AsyncMock) as mock_discogs:
            mock_discogs.return_value = []
            await process_track(_make_track(), cache=cache)
            await process_track(_make_track(), cache=cache)
    assert mock_mb.call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_process_track_writes_bpm_filtered_styles_to_mix(tmp_path: Path) -> None:
    """Discogs styles compatible with the track BPM should appear in match.mix."""
    cache = EnrichmentCache(tmp_path / "cache.json")
    # Track at 130 BPM — Drum n Bass (158-185) must be filtered out, Breakbeat kept
    track = TrackRecord(
        track_id="1",
        name="Some Track",
        artist="DJ Example",
        genre="Breakbeat",
        bpm=130.0,
        tonality="4A",
        duration_seconds=360,
    )
    candidate = CandidateMatch(
        source="discogs",
        source_id="x",
        artist="DJ Example",
        title="Some Track",
        label="Punks",
        year="2021",
        confidence=0.0,
        duration_seconds=362,
        styles=["Breakbeat", "Drum n Bass", "Speed Garage"],
    )
    with patch("enricher.enricher.lookup_musicbrainz", new_callable=AsyncMock) as mock_mb:
        mock_mb.return_value = [candidate]
        with patch("enricher.enricher.lookup_discogs", new_callable=AsyncMock) as mock_discogs:
            mock_discogs.return_value = []
            decision = await process_track(track, cache=cache)
    assert decision.status == "enriched"
    assert decision.match is not None
    assert "Breakbeat" in decision.match.mix
    assert "Speed Garage" in decision.match.mix
    assert "Drum n Bass" not in decision.match.mix


@pytest.mark.asyncio
async def test_process_track_no_llm_skips_low_confidence(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    low_conf = CandidateMatch(
        source="musicbrainz",
        source_id="y",
        artist="DJ Example",
        title="Some Track",
        label="Defected",
        year="2021",
        confidence=0.0,
        duration_seconds=500,  # far from track duration → low confidence
    )
    with patch("enricher.enricher.lookup_musicbrainz", new_callable=AsyncMock) as mock_mb:
        mock_mb.return_value = [low_conf]
        with patch("enricher.enricher.lookup_discogs", new_callable=AsyncMock) as mock_discogs:
            mock_discogs.return_value = []
            decision = await process_track(_make_track(), cache=cache, use_llm=False)
    assert decision.status in ("skipped_low_confidence", "skipped_no_match", "enriched")
