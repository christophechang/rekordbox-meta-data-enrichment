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

# BPM ranges for common electronic music styles sourced from Discogs.
# Tracks outside a style's range are filtered out — e.g. a 130 BPM track cannot be Drum n Bass.
# Styles absent from this map are kept (unknown = don't discard).
_STYLE_BPM_RANGES: dict[str, tuple[int, int]] = {
    # House family
    "House": (118, 132),
    "Deep House": (118, 128),
    "Tech House": (122, 135),
    "Funky House": (118, 130),
    "Soulful House": (118, 130),
    "Disco House": (115, 128),
    "Electro House": (124, 135),
    "Progressive House": (124, 135),
    # Techno
    "Techno": (130, 155),
    "Minimal Techno": (128, 145),
    "Detroit Techno": (128, 150),
    "Industrial Techno": (128, 155),
    "Industrial": (118, 160),
    # Trance
    "Trance": (128, 150),
    "Progressive Trance": (128, 145),
    "Psytrance": (138, 150),
    "Uplifting Trance": (135, 150),
    # Breaks / Jungle / DnB
    "Breakbeat": (115, 145),
    "Breaks": (115, 145),
    "Jungle": (150, 175),
    "Drum n Bass": (158, 185),
    "Liquid Funk": (162, 180),
    "Neurofunk": (162, 185),
    # UK Garage / Dubstep
    "UK Garage": (128, 142),
    "Speed Garage": (128, 140),
    "2-Step": (128, 140),
    "Grime": (133, 145),
    "Dubstep": (135, 145),
    # Rave / Hardcore
    "Rave": (130, 165),
    "Hardcore": (155, 200),
    "Happy Hardcore": (155, 200),
    "Gabber": (160, 230),
    # Electro / Electronic
    "Electro": (108, 128),
    "Electronica": (90, 145),
    "IDM": (80, 165),
    # Ambient / Downtempo
    "Ambient": (60, 110),
    "Downtempo": (75, 105),
    "Trip Hop": (75, 100),
    # Disco / Funk
    "Disco": (110, 128),
    "Nu-Disco": (112, 126),
    "Funk": (80, 125),
    # Hip Hop
    "Hip Hop": (75, 105),
    "Hip-Hop": (75, 105),
    "Trap": (60, 85),
    # Soul / R&B
    "Soul": (70, 120),
    "R&B": (60, 105),
    # Reggae / Dub
    "Reggae": (60, 100),
    "Dub": (60, 100),
    "Dancehall": (60, 100),
}


def filter_styles_by_bpm(styles: list[str], bpm: float) -> list[str]:
    """Return only styles whose typical BPM range includes the given BPM.

    Styles not in ``_STYLE_BPM_RANGES`` are kept — unknown does not mean incompatible.
    """
    if not bpm:
        return styles
    result = []
    for style in styles:
        bounds = _STYLE_BPM_RANGES.get(style)
        if bounds is None or bounds[0] <= bpm <= bounds[1]:
            result.append(style)
    return result


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
