from __future__ import annotations

import pytest

from enricher.models import CandidateMatch, TrackRecord


@pytest.fixture
def house_track() -> TrackRecord:
    return TrackRecord(
        track_id="1001",
        name="Some Track",
        artist="DJ Example",
        genre="House",
        bpm=125.0,
        tonality="4A",
        label="",
        year="",
        duration_seconds=360,
    )


@pytest.fixture
def mb_candidate() -> CandidateMatch:
    return CandidateMatch(
        source="musicbrainz",
        source_id="abc-123",
        artist="DJ Example",
        title="Some Track",
        label="Defected",
        year="2021",
        remixer="",
        album="Some Album",
        mix="Original Mix",
        duration_seconds=362,
        confidence=0.0,
    )
