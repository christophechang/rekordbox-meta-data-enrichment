from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from enricher.lookup import lookup_discogs, lookup_musicbrainz
from enricher.models import TrackRecord

_TRACK = TrackRecord(
    track_id="1",
    name="Some Track",
    artist="DJ Example",
    genre="House",
    bpm=125.0,
    tonality="4A",
    duration_seconds=360,
)

_MB_RESPONSE = {
    "recordings": [
        {
            "id": "mb-abc-123",
            "title": "Some Track",
            "length": 360000,
            "artist-credit": [{"artist": {"name": "DJ Example"}}],
            "releases": [
                {
                    "title": "Some Album",
                    "date": "2021-05-01",
                    "label-info": [{"label": {"name": "Defected"}}],
                }
            ],
            "relations": [{"type": "remixer", "artist": {"name": "Remixer X"}}],
        }
    ]
}

_DISCOGS_RESPONSE = {
    "results": [
        {
            "id": 12345,
            "title": "DJ Example - Some Track",
            "year": "2021",
            "label": ["Defected"],
            "format": ['12"'],
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_lookup_musicbrainz_returns_candidates() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").mock(
        return_value=Response(200, content=json.dumps(_MB_RESPONSE).encode())
    )
    candidates = await lookup_musicbrainz(_TRACK)
    assert len(candidates) == 1
    assert candidates[0].source == "musicbrainz"
    assert candidates[0].label == "Defected"
    assert candidates[0].year == "2021"
    assert candidates[0].remixer == "Remixer X"
    assert candidates[0].album == "Some Album"
    assert candidates[0].duration_seconds == 360


@pytest.mark.asyncio
@respx.mock
async def test_lookup_musicbrainz_returns_empty_on_error() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").mock(return_value=Response(500))
    candidates = await lookup_musicbrainz(_TRACK)
    assert candidates == []


@pytest.mark.asyncio
@respx.mock
async def test_lookup_discogs_returns_candidates() -> None:
    respx.get("https://api.discogs.com/database/search").mock(
        return_value=Response(200, content=json.dumps(_DISCOGS_RESPONSE).encode())
    )
    candidates = await lookup_discogs(_TRACK, token=None)
    assert len(candidates) == 1
    assert candidates[0].source == "discogs"
    assert candidates[0].label == "Defected"
    assert candidates[0].year == "2021"
    assert candidates[0].artist == "DJ Example"
    assert candidates[0].title == "Some Track"


@pytest.mark.asyncio
@respx.mock
async def test_lookup_discogs_returns_empty_on_error() -> None:
    respx.get("https://api.discogs.com/database/search").mock(return_value=Response(429))
    candidates = await lookup_discogs(_TRACK, token=None)
    assert candidates == []
