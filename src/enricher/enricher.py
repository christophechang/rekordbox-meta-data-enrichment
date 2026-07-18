from __future__ import annotations

import sys

from enricher.cache import CacheProtocol
from enricher.disambiguator import disambiguate
from enricher.lookup import lookup_discogs, lookup_musicbrainz, mb_recording_details
from enricher.models import CandidateMatch, EnrichmentDecision, TrackRecord
from enricher.scorer import score_all

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


# The only fields enrichment may ever propose. Fill-blank-only: a change exists
# only when the track's field is empty — non-empty values are never replaced.
_ENRICHABLE = ("label", "year", "remixer")


def _fields_changed(track: TrackRecord, match: CandidateMatch) -> dict[str, tuple[str, str]]:
    changes: dict[str, tuple[str, str]] = {}
    for field in _ENRICHABLE:
        old = str(getattr(track, field))
        new = str(getattr(match, field))
        if new and not old:
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
    if _is_already_complete(track):
        return EnrichmentDecision(
            track_id=track.track_id,
            artist=track.artist,
            title=track.name,
            status="skipped_already_complete",
        )

    cached = cache.get(track.artist, track.name)
    cache_hit = cached is not None
    candidates: list[CandidateMatch] = list(cached) if cached is not None else []

    if not cache_hit:
        # --- Live lookup ---
        try:
            if sources in ("musicbrainz", "both"):
                candidates.extend(await lookup_musicbrainz(track))

            scored_probe = score_all(track, candidates)
            best_score = scored_probe[0].confidence if scored_probe else 0.0
            best_has_label = bool(scored_probe[0].label) if scored_probe else False
            if sources in ("discogs", "both") and (best_score < confidence_threshold or not best_has_label):
                candidates.extend(await lookup_discogs(track, token=discogs_token))

            # MB search can't supply label/remixer — fetch details once when the winner needs them
            probe = score_all(track, candidates)
            if probe and probe[0].source == "musicbrainz" and probe[0].source_id:
                best_probe = probe[0]
                needs_label = not track.label and not best_probe.label
                needs_remixer = not track.remixer and not best_probe.remixer
                if needs_label or needs_remixer:
                    label, remixer = await mb_recording_details(best_probe.source_id)
                    for i, c in enumerate(candidates):
                        if c.source == "musicbrainz" and c.source_id == best_probe.source_id:
                            candidates[i] = c.model_copy(
                                update={"label": c.label or label, "remixer": c.remixer or remixer}
                            )
                            break
        except Exception as exc:
            print(f"ERROR lookup failed for {track.artist} — {track.name}: {exc}", file=sys.stderr)
            return EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="skipped_api_error",
            )
        cache.put(track.artist, track.name, candidates)

    scored = score_all(track, candidates)

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
                cache_hit=cache_hit,
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
                cache_hit=cache_hit,
            )
        return decision

    best = _fill_label(scored[0], candidates)

    # --- Auto-enrich path (high confidence) ---
    if best.confidence >= confidence_threshold:
        changed = _fields_changed(track, best)
        if not changed:
            decision = EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="skipped_already_complete",
                cache_hit=cache_hit,
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
                cache_hit=cache_hit,
            )
        return decision

    # --- LLM disambiguation path ---
    if use_llm and best.confidence >= _DISAMBIG_LOW:
        ambiguous = [c for c in scored if c.confidence >= _DISAMBIG_LOW]
        chosen_idx, provider = await disambiguate(track, ambiguous)
        if chosen_idx >= 0 and provider is not None:
            chosen = _fill_label(ambiguous[chosen_idx], candidates)
            changed = _fields_changed(track, chosen)
            if not changed:
                decision = EnrichmentDecision(
                    track_id=track.track_id,
                    artist=track.artist,
                    title=track.name,
                    status="skipped_already_complete",
                    cache_hit=cache_hit,
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
                    cache_hit=cache_hit,
                )
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
                cache_hit=cache_hit,
            )
            return decision

    # --- Low confidence fallthrough ---
    decision = EnrichmentDecision(
        track_id=track.track_id,
        artist=track.artist,
        title=track.name,
        status="skipped_low_confidence",
        match=best,
        clear_colour=colour_confidence,
        cache_hit=cache_hit,
    )
    return decision
