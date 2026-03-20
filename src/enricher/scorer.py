from __future__ import annotations

import re
import unicodedata

from enricher.models import CandidateMatch, TrackRecord

# Remix suffix patterns to strip when comparing titles
_REMIX_RE = re.compile(
    r"\s*[\(\[]"
    r"(?:original|club|radio|extended|instrumental|dub|vocal|mix|edit|version|vip|reprise|bootleg|rework|remix)"
    r"[^\)\]]*[\)\]]",
    re.IGNORECASE,
)

# Genre family groupings for the bonus score
_GENRE_FAMILIES: list[frozenset[str]] = [
    frozenset({"house", "deep house", "tech house", "afro house", "funky house", "soulful house", "disco house"}),
    frozenset({"techno", "industrial techno", "minimal techno", "detroit techno"}),
    frozenset({"drum & bass", "dnb", "drum and bass", "liquid funk", "neurofunk", "jump up"}),
    frozenset({"trance", "progressive trance", "psytrance", "uplifting trance"}),
    frozenset({"uk garage", "garage", "speed garage", "2-step"}),
    frozenset({"jungle", "breakbeat", "breaks"}),
    frozenset({"electronic", "electronica", "electro"}),
]


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_remix(title: str) -> str:
    return _REMIX_RE.sub("", title).strip()


def _artist_score(track_artist: str, candidate_artist: str) -> float:
    if not track_artist or not candidate_artist:
        return 0.0
    norm_track = _normalise(track_artist)
    norm_cand = _normalise(candidate_artist)
    if norm_track == norm_cand:
        return 0.40
    # Check if one is contained in the other (handles "Artist feat. X" vs "Artist")
    if norm_track in norm_cand or norm_cand in norm_track:
        return 0.35
    # Token overlap
    track_tokens = set(norm_track.split())
    cand_tokens = set(norm_cand.split())
    if not track_tokens or not cand_tokens:
        return 0.0
    overlap = len(track_tokens & cand_tokens) / max(len(track_tokens), len(cand_tokens))
    return round(0.30 * overlap, 4)


def _title_score(track_title: str, candidate_title: str) -> float:
    if not track_title or not candidate_title:
        return 0.0
    norm_track = _normalise(track_title)
    norm_cand = _normalise(candidate_title)
    if norm_track == norm_cand:
        return 0.40
    # Compare with remix suffixes stripped
    stripped_track = _normalise(_strip_remix(track_title))
    stripped_cand = _normalise(_strip_remix(candidate_title))
    if stripped_track == stripped_cand:
        return 0.30
    # Token overlap on stripped titles
    track_tokens = set(stripped_track.split())
    cand_tokens = set(stripped_cand.split())
    if not track_tokens or not cand_tokens:
        return 0.0
    overlap = len(track_tokens & cand_tokens) / max(len(track_tokens), len(cand_tokens))
    return round(0.25 * overlap, 4)


def _duration_score(track_secs: int, candidate_secs: int | None) -> float:
    if not track_secs or not candidate_secs:
        return 0.0
    diff = abs(track_secs - candidate_secs)
    if diff <= 5:
        return 0.15
    if diff <= 15:
        return 0.08
    return 0.0


def _genre_bonus(track_genre: str, candidate_source: str) -> float:
    norm = track_genre.lower()
    for family in _GENRE_FAMILIES:
        if norm in family:
            # Electronic music is better covered by Discogs
            if candidate_source == "discogs":
                return 0.05
            return 0.03
    return 0.0


def score_candidate(track: TrackRecord, candidate: CandidateMatch) -> float:
    score = (
        _artist_score(track.artist, candidate.artist)
        + _title_score(track.name, candidate.title)
        + _duration_score(track.duration_seconds, candidate.duration_seconds)
        + _genre_bonus(track.genre, candidate.source)
    )
    return min(round(score, 4), 1.0)


def score_all(track: TrackRecord, candidates: list[CandidateMatch]) -> list[CandidateMatch]:
    scored = []
    for c in candidates:
        updated = c.model_copy(update={"confidence": score_candidate(track, c)})
        scored.append(updated)
    return sorted(scored, key=lambda c: c.confidence, reverse=True)
