from __future__ import annotations

from enricher.models import CandidateMatch, TrackRecord


def _track(**overrides: object) -> TrackRecord:
    base: dict[str, object] = {
        "track_id": "1",
        "name": "Keep On",
        "artist": "Denham Audio",
        "genre": "Breakbeat",
        "bpm": 140.0,
        "tonality": "2A",
        "duration_seconds": 286,
    }
    base.update(overrides)
    return TrackRecord(**base)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> CandidateMatch:
    base: dict[str, object] = {
        "source": "discogs",
        "source_id": "123",
        "artist": "Denham Audio",
        "title": "Keep On",
    }
    base.update(overrides)
    return CandidateMatch(**base)  # type: ignore[arg-type]
