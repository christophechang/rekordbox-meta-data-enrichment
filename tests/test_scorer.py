from __future__ import annotations

import pytest

from enricher.models import CandidateMatch, TrackRecord
from enricher.scorer import score_all, score_candidate


def _make_track(artist: str = "DJ Example", title: str = "Some Track", duration: int = 360) -> TrackRecord:
    return TrackRecord(
        track_id="1",
        name=title,
        artist=artist,
        genre="House",
        bpm=125.0,
        tonality="4A",
        duration_seconds=duration,
    )


def _make_candidate(
    artist: str = "DJ Example",
    title: str = "Some Track",
    duration: int | None = 360,
    source: str = "musicbrainz",
) -> CandidateMatch:
    return CandidateMatch(
        source=source,  # type: ignore[arg-type]
        source_id="x",
        artist=artist,
        title=title,
        duration_seconds=duration,
    )


def test_score_exact_match_returns_high_confidence() -> None:
    track = _make_track()
    candidate = _make_candidate()
    score = score_candidate(track, candidate)
    assert score >= 0.85


def test_score_wrong_artist_returns_low_confidence() -> None:
    track = _make_track(artist="DJ Example")
    candidate = _make_candidate(artist="Completely Different Artist")
    score = score_candidate(track, candidate)
    assert score < 0.65


def test_score_duration_within_5s_adds_bonus() -> None:
    track = _make_track(duration=360)
    exact = _make_candidate(duration=362)
    far = _make_candidate(duration=500)
    assert score_candidate(track, exact) > score_candidate(track, far)


def test_score_remix_title_stripped_for_comparison() -> None:
    track = _make_track(title="Some Track")
    candidate = _make_candidate(title="Some Track (Original Mix)")
    score = score_candidate(track, candidate)
    assert score >= 0.65


def test_score_all_returns_sorted_descending(house_track: TrackRecord, mb_candidate: CandidateMatch) -> None:
    poor = CandidateMatch(source="musicbrainz", source_id="y", artist="Nobody", title="Unrelated", confidence=0.0)
    scored = score_all(house_track, [poor, mb_candidate])
    assert scored[0].confidence >= scored[1].confidence


def test_score_discogs_electronic_gets_genre_bonus() -> None:
    track = _make_track()
    discogs = _make_candidate(source="discogs")
    mb = _make_candidate(source="musicbrainz")
    assert score_candidate(track, discogs) >= score_candidate(track, mb)


@pytest.mark.parametrize(
    ("artist", "title", "expected_min"),
    [
        ("DJ Example", "Some Track", 0.85),
        ("DJ Example", "Some Track (Club Mix)", 0.65),
        ("dj example", "some track", 0.85),  # case insensitive
    ],
)
def test_score_parametrized(artist: str, title: str, expected_min: float) -> None:
    track = _make_track()
    candidate = _make_candidate(artist=artist, title=title)
    assert score_candidate(track, candidate) >= expected_min
