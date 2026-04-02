from __future__ import annotations

from enricher.enricher import COLOUR_GREEN, COLOUR_ORANGE, COLOUR_RED
from enricher.models import EnrichmentDecision

_COLOUR_LABEL: dict[str | None, str] = {
    COLOUR_GREEN: "green",
    COLOUR_ORANGE: "orange",
    COLOUR_RED: "red",
    None: "",
}


def build_report(decisions: list[EnrichmentDecision]) -> str:
    enriched = [d for d in decisions if d.status == "enriched"]
    low_conf = [d for d in decisions if d.status == "skipped_low_confidence"]
    no_match = [d for d in decisions if d.status == "skipped_no_match"]
    api_error = [d for d in decisions if d.status == "skipped_api_error"]
    already_done = [d for d in decisions if d.status == "skipped_already_complete"]

    llm_counts: dict[str, int] = {}
    for d in enriched:
        if d.disambiguation_used is not None:
            llm_counts[d.disambiguation_used] = llm_counts.get(d.disambiguation_used, 0) + 1

    lines: list[str] = []

    lines.append("=" * 72)
    lines.append("REKORDBOX METADATA ENRICHMENT REPORT")
    lines.append("=" * 72)
    lines.append("")
    lines.append("SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total tracks processed : {len(decisions)}")
    enriched_live = [d for d in enriched if not d.cache_hit]
    enriched_cached = [d for d in enriched if d.cache_hit]
    lines.append(f"  Enriched               : {len(enriched)} (live: {len(enriched_live)}, cached: {len(enriched_cached)})")
    lines.append(f"  Already complete       : {len(already_done)}")
    lines.append(f"  Skipped (low conf.)    : {len(low_conf)}")
    lines.append(f"  Skipped (no match)     : {len(no_match)}")
    lines.append(f"  Skipped (API error)    : {len(api_error)}")
    if llm_counts:
        lines.append("")
        lines.append("LLM DISAMBIGUATION CALLS")
        lines.append("-" * 40)
        for provider, count in sorted(llm_counts.items()):
            lines.append(f"  {provider:<12}: {count}")

    if enriched:
        lines.append("")
        lines.append("ENRICHED TRACKS")
        lines.append("-" * 72)
        for d in enriched:
            assert d.match is not None  # noqa: S101 — status==enriched guarantees match is set
            parts: list[str] = []
            if d.disambiguation_used is not None:
                parts.append(d.disambiguation_used)
            colour_label = _COLOUR_LABEL.get(d.confidence_colour, "")
            if colour_label:
                parts.append(colour_label)
            tag = f"[{', '.join(parts)}]" if parts else ""
            lines.append(f"  {d.artist} — {d.title} {tag}".rstrip())
            for field, (old, new) in d.fields_changed.items():
                old_display = old if old else "(empty)"
                lines.append(f"    {field:<10}: {old_display!r:30} → {new!r}")

    if low_conf:
        lines.append("")
        lines.append("UNRESOLVED — LOW CONFIDENCE (review manually)")
        lines.append("-" * 72)
        for d in low_conf:
            best_info = ""
            if d.match:
                best_info = (
                    f" | best: {d.match.artist} — {d.match.title} ({d.match.source}, conf={d.match.confidence:.2f})"
                )
            lines.append(f"  {d.artist} — {d.title}{best_info}")

    if no_match:
        lines.append("")
        lines.append("UNRESOLVED — NO MATCH FOUND")
        lines.append("-" * 72)
        for d in no_match:
            lines.append(f"  {d.artist} — {d.title}")

    if api_error:
        lines.append("")
        lines.append("SKIPPED — API ERRORS")
        lines.append("-" * 72)
        for d in api_error:
            lines.append(f"  {d.artist} — {d.title}")

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)
