from __future__ import annotations

import sys

from enricher.cache import CacheProtocol
from enricher.disambiguator import disambiguate
from enricher.lookup import lookup_discogs, lookup_musicbrainz
from enricher.models import CandidateMatch, EnrichmentDecision, TrackRecord
from enricher.scorer import filter_styles_by_bpm, score_all

# Keywords in artist or title that indicate an unofficial release, checked when no API match found.
# Ordered by specificity — first match wins.
_HEURISTIC_LABELS: list[tuple[str, str]] = [
    ("white label", "White Label"),
    ("bootleg", "Bootleg"),
    ("unofficial", "Bootleg"),
    ("free dl", "Bootleg"),
    ("free download", "Bootleg"),
]

_AUTO_THRESHOLD = 0.85
_DISAMBIG_LOW = 0.65  # minimum score to attempt LLM disambiguation
_COLOUR_MIN = 0.30  # below this, don't apply even in colour-confidence mode

# Rekordbox colour hex values used as confidence signals
COLOUR_GREEN = "0x00FF00"  # high confidence — safe to use
COLOUR_ORANGE = "0xFFA500"  # medium confidence — worth reviewing
COLOUR_RED = "0xFF0000"  # low confidence — inspect carefully


def _fill_label(best: CandidateMatch, all_candidates: list[CandidateMatch]) -> CandidateMatch:
    """If the best candidate has no label, borrow one from the highest-confidence labeled candidate.

    MusicBrainz often wins on title/artist/year scoring but lacks label data for underground
    releases. Discogs has the label but scores lower. This merges the best of both sources.
    """
    if best.label:
        return best
    labeled = [c for c in all_candidates if c.label]
    if not labeled:
        return best
    # Prefer Discogs labels — more reliable for electronic music imprints
    discogs_labeled = [c for c in labeled if c.source == "discogs"]
    donor = max(discogs_labeled or labeled, key=lambda c: c.confidence)
    return best.model_copy(update={"label": donor.label})


def _heuristic_label(track: TrackRecord) -> str | None:
    """Return a label string if artist or title contains a known unofficial-release keyword."""
    haystack = f"{track.artist} {track.name}".lower()
    for keyword, label in _HEURISTIC_LABELS:
        if keyword in haystack:
            return label
    return None


def _apply_styles(track: TrackRecord, best: CandidateMatch, all_candidates: list[CandidateMatch]) -> CandidateMatch:
    """Populate the mix field with BPM-compatible Discogs style tags when mix is unused.

    Collects styles from the best match (or borrows from any Discogs candidate if the best
    has none), filters by the track's BPM, and writes the result as a comma-separated string.
    Skipped if the track already has a mix designation or no compatible styles are found.
    """
    if track.mix:
        return best
    styles = best.styles
    if not styles:
        discogs_with_styles = [c for c in all_candidates if c.source == "discogs" and c.styles]
        if discogs_with_styles:
            styles = max(discogs_with_styles, key=lambda c: c.confidence).styles
    compatible = filter_styles_by_bpm(styles, track.bpm)
    if not compatible:
        return best
    return best.model_copy(update={"mix": ", ".join(compatible)})


def _fields_changed(track: TrackRecord, match: CandidateMatch) -> dict[str, tuple[str, str]]:
    changes: dict[str, tuple[str, str]] = {}
    field_pairs: list[tuple[str, str, str]] = [
        ("label", track.label, match.label),
        ("year", track.year, match.year),
        ("remixer", track.remixer, match.remixer),
        ("album", track.album, match.album),
        ("mix", track.mix, match.mix),
    ]
    for field, old, new in field_pairs:
        if new and new != old:
            changes[field] = (old, new)
    return changes


def _is_already_complete(track: TrackRecord) -> bool:
    return bool(track.label and track.year)


async def process_track(
    track: TrackRecord,
    cache: CacheProtocol,
    sources: str = "both",
    confidence_threshold: float = _AUTO_THRESHOLD,
    use_llm: bool = True,
    discogs_token: str | None = None,
    colour_confidence: bool = False,
) -> EnrichmentDecision:
    cached = cache.get(track.artist, track.name)
    if cached is not None:
        _candidates, decision = cached
        return decision

    if _is_already_complete(track):
        decision = EnrichmentDecision(
            track_id=track.track_id,
            artist=track.artist,
            title=track.name,
            status="skipped_already_complete",
        )
        cache.put(track.artist, track.name, [], decision)
        return decision

    # --- Lookup ---
    candidates: list[CandidateMatch] = []
    try:
        if sources in ("musicbrainz", "both"):
            mb_candidates = await lookup_musicbrainz(track)
            candidates.extend(mb_candidates)

        scored = score_all(track, candidates)
        best_mb_score = scored[0].confidence if scored else 0.0

        best_mb_has_label = bool(scored[0].label) if scored else False
        if sources in ("discogs", "both") and (best_mb_score < confidence_threshold or not best_mb_has_label):
            discogs_candidates = await lookup_discogs(track, token=discogs_token)
            candidates.extend(discogs_candidates)
            scored = score_all(track, candidates)

    except Exception as exc:
        print(f"ERROR lookup failed for {track.artist} — {track.name}: {exc}", file=sys.stderr)
        return EnrichmentDecision(
            track_id=track.track_id,
            artist=track.artist,
            title=track.name,
            status="skipped_api_error",
        )

    if not scored:
        heuristic = _heuristic_label(track)
        if heuristic and not track.label:
            synthetic = CandidateMatch(
                source="musicbrainz",
                source_id="",
                artist=track.artist,
                title=track.name,
                label=heuristic,
                confidence=1.0,
            )
            changed = _fields_changed(track, synthetic)
            decision = EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="enriched",
                match=synthetic,
                fields_changed=changed,
                confidence_colour=COLOUR_RED if colour_confidence else None,
            )
        else:
            # skipped_no_match is never cached — retried on every run so query
            # improvements automatically pick up previously missed tracks
            return EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="skipped_no_match",
                clear_colour=colour_confidence,
            )
        cache.put(track.artist, track.name, [], decision)
        return decision

    best = _apply_styles(track, _fill_label(scored[0], candidates), candidates)

    # --- Auto-enrich path (high confidence) ---
    if best.confidence >= confidence_threshold:
        changed = _fields_changed(track, best)
        if not changed:
            decision = EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="skipped_already_complete",
            )
        else:
            colour = COLOUR_GREEN if colour_confidence else None
            decision = EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="enriched",
                match=best,
                fields_changed=changed,
                confidence_colour=colour,
            )
        cache.put(track.artist, track.name, scored, decision)
        return decision

    # --- LLM disambiguation path ---
    if use_llm and best.confidence >= _DISAMBIG_LOW:
        ambiguous = [c for c in scored if c.confidence >= _DISAMBIG_LOW]
        chosen_idx, provider = await disambiguate(track, ambiguous)
        if chosen_idx >= 0 and provider is not None:
            chosen = _apply_styles(track, _fill_label(ambiguous[chosen_idx], candidates), candidates)
            changed = _fields_changed(track, chosen)
            if not changed:
                decision = EnrichmentDecision(
                    track_id=track.track_id,
                    artist=track.artist,
                    title=track.name,
                    status="skipped_already_complete",
                )
            else:
                colour = COLOUR_ORANGE if colour_confidence else None
                decision = EnrichmentDecision(
                    track_id=track.track_id,
                    artist=track.artist,
                    title=track.name,
                    status="enriched",
                    match=chosen,
                    fields_changed=changed,
                    disambiguation_used=provider,
                    confidence_colour=colour,
                )
            cache.put(track.artist, track.name, scored, decision)
            return decision

    # --- Colour-confidence mode: apply low-confidence matches with red ---
    if colour_confidence and best.confidence >= _COLOUR_MIN:
        changed = _fields_changed(track, best)
        if changed:
            decision = EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="enriched",
                match=best,
                fields_changed=changed,
                confidence_colour=COLOUR_RED,
            )
            cache.put(track.artist, track.name, scored, decision)
            return decision

    # --- Low confidence fallthrough ---
    decision = EnrichmentDecision(
        track_id=track.track_id,
        artist=track.artist,
        title=track.name,
        status="skipped_low_confidence",
        match=best,
        clear_colour=colour_confidence,
    )
    cache.put(track.artist, track.name, scored, decision)
    return decision
