from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TrackRecord(BaseModel):
    track_id: str
    name: str
    artist: str
    genre: str
    bpm: float
    tonality: str
    label: str = ""
    year: str = ""
    remixer: str = ""
    album: str = ""
    mix: str = ""
    duration_seconds: int = 0


class CandidateMatch(BaseModel):
    source: Literal["musicbrainz", "discogs"]
    source_id: str
    artist: str
    title: str
    label: str = ""
    year: str = ""
    remixer: str = ""
    album: str = ""
    mix: str = ""
    duration_seconds: int | None = None
    confidence: float = 0.0


class EnrichmentDecision(BaseModel):
    track_id: str
    artist: str
    title: str
    status: Literal[
        "enriched",
        "skipped_low_confidence",
        "skipped_no_match",
        "skipped_api_error",
        "skipped_already_complete",
    ]
    match: CandidateMatch | None = None
    fields_changed: dict[str, tuple[str, str]] = Field(default_factory=dict)
    disambiguation_used: Literal["minimax", "groq", "gemini"] | None = None
    confidence_colour: str | None = None  # set when --colour-confidence is active
    clear_colour: bool = False  # explicitly blank the Colour field (no usable match in colour-confidence mode)
