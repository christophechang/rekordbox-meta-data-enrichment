from __future__ import annotations

import json

import httpx
import pytest
import respx
from factories import _track
from httpx import Response

from enricher.lookup import (
    SourceLookupError,
    _clean_title,
    _escape_lucene,
    _extract_discogs_candidates,
    _primary_artist,
    _strip_mix_designators,
    discogs_master_year,
    has_remix_designator,
    lookup_discogs,
    lookup_musicbrainz,
    mb_recording_details,
)
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
            "first-release-date": "2021-05-01",
            "artist-credit": [{"artist": {"name": "DJ Example"}}],
            # Realistic: release stubs have title/date/release-group but NO label-info, NO relations
            "releases": [
                {"title": "Some Album", "date": "2021-05-01", "release-group": {"secondary-types": []}},
            ],
        }
    ]
}

_MB_SEARCH_RESPONSE = {
    "recordings": [
        {
            "id": "mbid-1",
            "title": "Ladbroke Grove",
            "length": 372000,
            "first-release-date": "1997-06-02",
            "artist-credit": [{"artist": {"name": "Kerri Chandler"}}],
            # Realistic: release stubs have title/date/release-group but NO label-info, NO relations
            "releases": [
                {"title": "Hemisphere", "date": "1997-06-02", "release-group": {"secondary-types": []}},
            ],
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
    assert candidates[0].label == ""  # search cannot supply labels — detail lookup does (Task 5)
    assert candidates[0].year == "2021"
    assert candidates[0].remixer == ""
    assert candidates[0].album == "Some Album"
    assert candidates[0].duration_seconds == 360


@pytest.mark.asyncio
@respx.mock
async def test_lookup_musicbrainz_raises_on_http_error() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").mock(return_value=Response(500))
    with pytest.raises(SourceLookupError):
        await lookup_musicbrainz(_TRACK)


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
    """MB should pick the non-compilation release for album even if it appears later.

    label-info is omitted (search responses never carry it — realistic shape); year is
    independently sourced from the recording-level first-release-date, not any release date.
    """
    response = {
        "recordings": [
            {
                "id": "mb-001",
                "title": "Some Track",
                "length": 360000,
                "first-release-date": "2018-03-10",
                "artist-credit": [{"artist": {"name": "DJ Example"}}],
                "releases": [
                    {
                        "title": "Ministry of Sound Compilation",
                        "date": "2019",
                        "release-group": {"secondary-types": ["Compilation"]},
                    },
                    {
                        "title": "Original EP",
                        "date": "2018",
                        "release-group": {"secondary-types": []},
                    },
                ],
            }
        ]
    }
    respx.get("https://musicbrainz.org/ws/2/recording/").mock(
        return_value=Response(200, content=json.dumps(response).encode())
    )
    candidates = await lookup_musicbrainz(_TRACK)
    assert candidates[0].label == ""
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
async def test_lookup_discogs_raises_source_lookup_error() -> None:
    respx.get("https://api.discogs.com/database/search").mock(return_value=Response(429))
    with pytest.raises(SourceLookupError) as exc_info:
        await lookup_discogs(_TRACK, token=None)
    assert exc_info.value.source == "discogs"


# ---------------------------------------------------------------------------
# MB search repairs: no-op inc param dropped, Lucene escaping, honest rate
# limit, and SourceLookupError surfaced on HTTP failure (Task 4)
# ---------------------------------------------------------------------------


@respx.mock
async def test_mb_year_comes_from_first_release_date() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(json=_MB_SEARCH_RESPONSE)
    track = _track(name="Ladbroke Grove", artist="Kerri Chandler")
    results = await lookup_musicbrainz(track)
    assert results[0].year == "1997"
    assert results[0].label == ""  # search cannot supply labels — detail lookup does (Task 5)
    assert results[0].remixer == ""


@respx.mock
async def test_mb_search_sends_no_inc_param_and_escapes_lucene() -> None:
    route = respx.get("https://musicbrainz.org/ws/2/recording/").respond(json={"recordings": []})
    track = _track(name='Who"s Afraid (2:1)', artist="John Tejada")
    await lookup_musicbrainz(track)
    sent = route.calls[0].request.url
    assert "inc=" not in str(sent)
    assert '\\"' in httpx.URL(sent).params["query"] or "\\:" in httpx.URL(sent).params["query"]


@respx.mock
async def test_mb_http_error_raises_source_lookup_error() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(status_code=400)
    with pytest.raises(SourceLookupError) as exc_info:
        await lookup_musicbrainz(_track(name="X", artist="Y"))
    assert exc_info.value.source == "musicbrainz"


@respx.mock
async def test_mb_503_retries_then_succeeds() -> None:
    route = respx.get("https://musicbrainz.org/ws/2/recording/")
    route.side_effect = [
        httpx.Response(503, headers={"Retry-After": "0"}),
        httpx.Response(200, json=_MB_SEARCH_RESPONSE),
    ]
    results = await lookup_musicbrainz(_track(name="Ladbroke Grove", artist="Kerri Chandler"))
    assert results and results[0].year == "1997"


@respx.mock
async def test_mb_fallback_ladder_fires_in_order() -> None:
    # Spec §7: attempts 1→2→3 with (full artist, clean title) → (primary artist, clean title)
    # → (primary artist, designator-stripped title), each firing only on empty results.
    route = respx.get("https://musicbrainz.org/ws/2/recording/").respond(json={"recordings": []})
    track = _track(name="Fall Down (Calibre Remix)", artist="Roni Size & Krust")
    await lookup_musicbrainz(track)
    queries = [httpx.URL(c.request.url).params["query"] for c in route.calls]
    assert len(queries) == 3
    assert "Krust" in queries[0]
    assert "Krust" not in queries[1] and "Roni Size" in queries[1]
    assert "Calibre" not in queries[2] and "Fall Down" in queries[2]


# ---------------------------------------------------------------------------
# MB recording-detail follow-up: label/remixer restored via the lookup
# endpoint, since search responses never carry them (Task 5)
# ---------------------------------------------------------------------------

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
async def test_mb_recording_details_extracts_label_and_remixer() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/mbid-1").respond(json=_MB_DETAIL_RESPONSE)
    label, remixer = await mb_recording_details("mbid-1")
    assert label == "Shelter Records"
    assert remixer == "DJ Deep"


def test_escape_lucene() -> None:
    assert _escape_lucene('Who"s Afraid') == 'Who\\"s Afraid'
    assert _escape_lucene("a+b(c)") == "a\\+b\\(c\\)"


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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Move Your Body (Shadow Child Extended Remix)", "Move Your Body"),
        ("Fall Down (Calibre Remix)", "Fall Down"),
        ("Track (Somebody's Flip)", "Track"),
    ],
)
def test_strip_mix_designators_handles_artist_remix(title: str, expected: str) -> None:
    assert _strip_mix_designators(title) == expected


# ---------------------------------------------------------------------------
# Unit tests for _clean_title / _TRAILING_BPM_RE (Task 6: MIK-artefact-only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Keep On 7A", "Keep On"),
        ("Keep On 7A 128", "Keep On"),
        ("Keep On 128 7A", "Keep On"),
        ("Xpander 2", "Xpander 2"),  # bare numbers are legitimate title content
        ("Vol. 3", "Vol. 3"),
    ],
)
def test_trailing_bpm_only_strips_mik_artefacts(title: str, expected: str) -> None:
    assert _clean_title(title) == expected


# ---------------------------------------------------------------------------
# Unit test for Discogs album extraction (Task 6: release title, not artist-prefixed)
# ---------------------------------------------------------------------------


def test_discogs_album_is_release_title_not_artist_prefixed() -> None:
    data: dict[str, object] = {
        "results": [{"id": 1, "title": "Kerri Chandler - Hemisphere", "year": 1997, "label": ["Shelter"]}]
    }
    cands = _extract_discogs_candidates(data, "Ladbroke Grove")
    assert cands[0].album == "Hemisphere"
    assert cands[0].artist == "Kerri Chandler"


# ---------------------------------------------------------------------------
# Remix-designator detection + Discogs master-year resolution (Task 7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Fall Down (Calibre Remix)", True),
        ("Track (VIP)", True),
        ("Track (Somebody's Flip)", True),
        ("Move Your Body (Extended Mix)", False),
        ("Ladbroke Grove", False),
        ("Song (Radio Edit)", False),
        ("Old Track (2015 Remastered)", False),
        ("Song (Prefix Mix)", False),
        ("Track (Premix Edit)", False),
        ("Trip To The Moon (VIP Dub)", True),
    ],
)
def test_has_remix_designator(title: str, expected: bool) -> None:
    assert has_remix_designator(title) is expected


@respx.mock
async def test_discogs_master_year_resolves_via_master() -> None:
    respx.get("https://api.discogs.com/releases/9001").respond(json={"id": 9001, "master_id": 555, "year": 2019})
    respx.get("https://api.discogs.com/masters/555").respond(json={"id": 555, "year": 1994})
    assert await discogs_master_year("9001", token=None) == "1994"


@respx.mock
async def test_discogs_master_year_falls_back_to_release_year() -> None:
    respx.get("https://api.discogs.com/releases/9001").respond(json={"id": 9001, "year": 2019})
    assert await discogs_master_year("9001", token=None) == "2019"


@respx.mock
async def test_discogs_master_year_soft_fails_on_http_error() -> None:
    respx.get("https://api.discogs.com/releases/9001").respond(status_code=500)
    assert await discogs_master_year("9001", token=None) == ""


@respx.mock
async def test_discogs_master_year_soft_fails_on_malformed_body() -> None:
    respx.get("https://api.discogs.com/releases/9001").respond(
        status_code=200, content=b"<html>gateway error</html>", headers={"Content-Type": "text/html"}
    )
    assert await discogs_master_year("9001", token=None) == ""
