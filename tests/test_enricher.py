from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import respx
from factories import _candidate, _track

from enricher.cache import EnrichmentCache, NullCache
from enricher.enricher import _fields_changed, process_track
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


class _SpyCache(NullCache):
    def __init__(self, candidates: list[CandidateMatch] | None = None) -> None:
        self.candidates = candidates
        self.put_calls: list[tuple[str, str, int]] = []

    def get(self, artist: str, title: str) -> list[CandidateMatch] | None:
        return self.candidates

    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None:
        self.put_calls.append((artist, title, len(candidates)))


@pytest.mark.asyncio
async def test_process_track_enriches_on_high_confidence(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "cache.json")
    with patch("enricher.enricher.lookup_musicbrainz", new_callable=AsyncMock) as mock_mb:
        mock_mb.return_value = [_high_conf_candidate()]
        with patch("enricher.enricher.lookup_discogs", new_callable=AsyncMock) as mock_discogs:
            mock_discogs.return_value = []
            with patch("enricher.enricher.mb_recording_details", new_callable=AsyncMock) as mock_detail:
                mock_detail.return_value = ("", "")
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
            with patch("enricher.enricher.mb_recording_details", new_callable=AsyncMock) as mock_detail:
                mock_detail.return_value = ("", "")
                await process_track(_make_track(), cache=cache)
                await process_track(_make_track(), cache=cache)
    assert mock_mb.call_count == 1  # second call served from cache


async def test_completeness_check_precedes_cache() -> None:
    # A track completed since the last run must short-circuit BEFORE any cache read,
    # so stale cached candidates can never re-clobber manual fixes.
    cache = _SpyCache(candidates=[_candidate(label="Wrong Label", year="1990")])
    track = _track(label="Correct Label", year="2001")
    decision = await process_track(track, cache=cache, sources="both", use_llm=False)
    assert decision.status == "skipped_already_complete"
    assert cache.put_calls == []


async def test_cached_candidates_replayed_with_current_track_id() -> None:
    cand = _candidate(label="Club Glow", year="2019", artist="Denham Audio", title="Keep On")
    cache = _SpyCache(candidates=[cand])
    track = _track(track_id="fresh-42", label="", year="")
    decision = await process_track(track, cache=cache, sources="both", use_llm=False, colour_confidence=True)
    assert decision.track_id == "fresh-42"  # never the id from a previous run
    assert decision.cache_hit is True
    assert decision.status == "enriched"


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
            with patch("enricher.enricher.mb_recording_details", new_callable=AsyncMock) as mock_detail:
                mock_detail.return_value = ("", "")
                decision = await process_track(_make_track(), cache=cache, use_llm=False)
    assert decision.status in ("skipped_low_confidence", "skipped_no_match", "enriched")


# ---------------------------------------------------------------------------
# MB recording-detail follow-up: process_track fetches label/remixer via the
# lookup endpoint only when the winning MB candidate needs them, and the
# merged result must be cached (Task 5)
# ---------------------------------------------------------------------------

_MB_SEARCH_RESPONSE = {
    "recordings": [
        {
            "id": "mbid-1",
            "title": "Ladbroke Grove",
            "length": 372000,
            "first-release-date": "1997-06-02",
            "artist-credit": [{"artist": {"name": "Kerri Chandler"}}],
            "releases": [
                {"title": "Hemisphere", "date": "1997-06-02", "release-group": {"secondary-types": []}},
            ],
        }
    ]
}

_MB_DETAIL_RESPONSE = {
    "id": "mbid-1",
    "title": "Ladbroke Grove",
    "releases": [
        {
            "title": "Hemisphere",
            "date": "1997-06-02",
            "release-group": {"secondary-types": []},
            "label-info": [{"label": {"name": "Shelter Records"}}],
        }
    ],
    "relations": [{"type": "remixer", "artist": {"name": "DJ Deep"}}],
}


@respx.mock
async def test_detail_lookup_only_fires_when_needed(tmp_path: Path) -> None:
    # Winning MB candidate already satisfies the track's blank fields → no detail call.
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(json=_MB_SEARCH_RESPONSE)
    detail_route = respx.get("https://musicbrainz.org/ws/2/recording/mbid-1").respond(json=_MB_DETAIL_RESPONSE)
    track = _track(name="Ladbroke Grove", artist="Kerri Chandler", label="Already Set", year="", remixer="Set Too")
    cache = EnrichmentCache(tmp_path / "c.json")
    await process_track(track, cache=cache, sources="musicbrainz", use_llm=False, colour_confidence=True)
    assert not detail_route.called


@respx.mock
async def test_detail_result_is_cached_with_candidates(tmp_path: Path) -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(json=_MB_SEARCH_RESPONSE)
    respx.get("https://musicbrainz.org/ws/2/recording/mbid-1").respond(json=_MB_DETAIL_RESPONSE)
    track = _track(name="Ladbroke Grove", artist="Kerri Chandler", label="", year="")
    cache = EnrichmentCache(tmp_path / "c.json")
    await process_track(track, cache=cache, sources="musicbrainz", use_llm=False, colour_confidence=True)
    cached = cache.get("Kerri Chandler", "Ladbroke Grove")
    assert cached is not None and cached[0].label == "Shelter Records"


def test_fields_changed_fills_blank_fields_only() -> None:
    track = _track(label="", year="2022", remixer="")
    match = _candidate(label="Club Glow", year="2019", remixer="Someone")
    changes = _fields_changed(track, match)
    assert changes == {"label": ("", "Club Glow"), "remixer": ("", "Someone")}
    # year is non-empty on the track: never replaced, even though the match disagrees


def test_fields_changed_never_proposes_album_or_mix() -> None:
    track = _track(label="", album="", mix="")
    match = _candidate(label="Club Glow", album="Some Album", mix="Club Mix")
    changes = _fields_changed(track, match)
    assert set(changes) == {"label"}


def test_fields_changed_empty_when_track_complete() -> None:
    track = _track(label="Hotflush", year="2015", remixer="")
    match = _candidate(label="Other", year="1999", remixer="")
    assert _fields_changed(track, match) == {}
