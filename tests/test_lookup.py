from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from enricher.lookup import _primary_artist, _strip_mix_designators, lookup_discogs, lookup_musicbrainz
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
async def test_lookup_discogs_extracts_style_tags() -> None:
    response = {
        "results": [
            {
                "id": 99,
                "title": "DJ Example - Some Track",
                "year": "2021",
                "label": ["Defected"],
                "format": ["Vinyl"],
                "style": ["Breakbeat", "Speed Garage"],
            }
        ]
    }
    respx.get("https://api.discogs.com/database/search").mock(
        return_value=Response(200, content=json.dumps(response).encode())
    )
    candidates = await lookup_discogs(_TRACK, token=None)
    assert candidates[0].styles == ["Breakbeat", "Speed Garage"]


@pytest.mark.asyncio
@respx.mock
async def test_lookup_musicbrainz_prefers_non_compilation_release() -> None:
    """MB should pick the non-compilation release for label/year even if it appears later."""
    response = {
        "recordings": [
            {
                "id": "mb-001",
                "title": "Some Track",
                "length": 360000,
                "artist-credit": [{"artist": {"name": "DJ Example"}}],
                "releases": [
                    {
                        "title": "Ministry of Sound Compilation",
                        "date": "2019",
                        "label-info": [{"label": {"name": "Ministry of Sound"}}],
                        "release-group": {
                            "primary-type": "Album",
                            "secondary-types": ["Compilation"],
                        },
                    },
                    {
                        "title": "Original EP",
                        "date": "2018",
                        "label-info": [{"label": {"name": "Defected"}}],
                        "release-group": {
                            "primary-type": "EP",
                            "secondary-types": [],
                        },
                    },
                ],
                "relations": [],
            }
        ]
    }
    respx.get("https://musicbrainz.org/ws/2/recording/").mock(
        return_value=Response(200, content=json.dumps(response).encode())
    )
    candidates = await lookup_musicbrainz(_TRACK)
    assert candidates[0].label == "Defected"
    assert candidates[0].year == "2018"
    assert candidates[0].album == "Original EP"


@pytest.mark.asyncio
@respx.mock
async def test_lookup_discogs_does_not_map_format_to_mix() -> None:
    """Discogs 'format' is the release medium (Vinyl, File, CD) — must never populate Mix."""
    response = {
        "results": [
            {
                "id": 99,
                "title": "DJ Example - Some Track",
                "year": "2021",
                "label": ["Defected"],
                "format": ["Vinyl", '12"', "LP"],
            }
        ]
    }
    respx.get("https://api.discogs.com/database/search").mock(
        return_value=Response(200, content=json.dumps(response).encode())
    )
    candidates = await lookup_discogs(_TRACK, token=None)
    assert len(candidates) == 1
    assert candidates[0].mix == ""


@pytest.mark.asyncio
@respx.mock
async def test_lookup_discogs_returns_empty_on_error() -> None:
    respx.get("https://api.discogs.com/database/search").mock(return_value=Response(429))
    candidates = await lookup_discogs(_TRACK, token=None)
    assert candidates == []


# ---------------------------------------------------------------------------
# Unit tests for _primary_artist helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("artist", "expected"),
    [
        ("DJ Example", "DJ Example"),
        ("DJ Example / Second Artist", "DJ Example"),
        ("DJ Example, Second Artist", "DJ Example"),
        ("DJ Example & Second Artist", "DJ Example"),
        ("DJ Example x Second Artist", "DJ Example"),
        ("DJ Example vs Second Artist", "DJ Example"),
        ("DJ Example feat. Second Artist", "DJ Example"),
        ("DJ Example feat Second Artist", "DJ Example"),
        ("DJ Example ft. Second Artist", "DJ Example"),
        ("DJ Example ft Second Artist", "DJ Example"),
        ("DJ Example presents Second Artist", "DJ Example"),
        ("DJ Example pres. Second Artist", "DJ Example"),
        ("DJ Example pres Second Artist", "DJ Example"),
        # case-insensitive matching must preserve original case of primary
        ("DJ Example Feat. Second Artist", "DJ Example"),
        ("DJ Example PRESENTS Second Artist", "DJ Example"),
    ],
)
def test_primary_artist_extracts_first_credit(artist: str, expected: str) -> None:
    assert _primary_artist(artist) == expected


# ---------------------------------------------------------------------------
# Unit tests for _strip_mix_designators helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Some Track (Original Mix)", "Some Track"),
        ("Some Track (Extended Mix)", "Some Track"),
        ("Some Track [Club Mix]", "Some Track"),
        ("Some Track (feat. Second Artist)", "Some Track"),
        ("Some Track (ft. Second Artist)", "Some Track"),
        # (Artist Presents X) — common for artist alias releases
        ("Terminator (Goldie Presents Rufige Kru)", "Terminator"),
        ("Blue Lines (Massive Attack Presents Mad Professor)", "Blue Lines"),
        # Bare title unchanged
        ("Some Track", "Some Track"),
    ],
)
def test_strip_mix_designators_removes_qualifiers(title: str, expected: str) -> None:
    assert _strip_mix_designators(title) == expected
