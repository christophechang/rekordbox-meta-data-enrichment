from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from enricher.cache import CacheProtocol, EnrichmentCache, NullCache
from enricher.enricher import process_track
from enricher.models import EnrichmentDecision
from enricher.reader import parse_collection
from enricher.reporter import build_report
from enricher.writer import write_enriched_xml

load_dotenv()

_DEFAULT_INPUT = Path("import/rekordbox.xml")


def _default_output() -> Path:
    return Path(f"export/rekordbox_export_{date.today().isoformat()}.xml")


_DEFAULT_CACHE = Path(".enrichment_cache.json")
_DEFAULT_CONFIDENCE = 0.85


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="enricher",
        description="Enrich a Rekordbox XML export with release metadata from MusicBrainz and Discogs.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT,
        help=f"Path to source Rekordbox XML (default: {_DEFAULT_INPUT})",
    )
    default_output = _default_output()
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Path for enriched output XML (default: export/rekordbox_export_YYYY-MM-DD.xml)",
    )
    parser.add_argument("--report", type=Path, default=None, help="Optional path for text report (default: stdout)")
    parser.add_argument(
        "--cache", type=Path, default=_DEFAULT_CACHE, help=f"Cache file path (default: {_DEFAULT_CACHE})"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing output files")
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=_DEFAULT_CONFIDENCE,
        metavar="FLOAT",
        help=f"Minimum confidence for auto-enrichment (default: {_DEFAULT_CONFIDENCE})",
    )
    parser.add_argument(
        "--sources",
        choices=["musicbrainz", "discogs", "both"],
        default="both",
        help="Which metadata sources to query (default: both)",
    )
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM disambiguation (auto-confidence only)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N tracks (useful for test runs)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache reads and writes (results are not persisted)",
    )
    parser.add_argument(
        "--no-colour-confidence",
        action="store_true",
        help=(
            "Disable colour confidence mode. By default all matches are applied and "
            "Rekordbox Colour is set as a confidence signal: green=safe, orange=review, "
            "red=uncertain. Pass this flag to only apply high-confidence matches."
        ),
    )
    parser.add_argument(
        "--full-export",
        action="store_true",
        help=(
            "Guarantee every track appears in exactly one output playlist. "
            "Enriched and already-complete tracks go into 'Updated Tracks'; "
            "no-match, low-confidence, and API-error tracks go into 'Unable to Enrich'. "
            "Use this to produce a definitive source-of-truth export."
        ),
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    tracks = parse_collection(args.input)
    if not tracks:
        print("No tracks found in collection. Exiting.", file=sys.stderr)
        return

    cache: CacheProtocol = NullCache() if args.no_cache else EnrichmentCache(args.cache)
    discogs_token: str | None = os.environ.get("DISCOGS_TOKEN") or None
    sources: str = args.sources
    colour_confidence: bool = not args.no_colour_confidence

    if args.limit is not None:
        tracks = tracks[: args.limit]

    decisions: list[EnrichmentDecision] = []
    total = len(tracks)

    for i, track in enumerate(tracks, 1):
        decision = await process_track(
            track,
            cache=cache,
            sources=sources,
            confidence_threshold=args.confidence_threshold,
            use_llm=not args.no_llm,
            discogs_token=discogs_token,
            colour_confidence=colour_confidence,
        )
        decisions.append(decision)

        if i % 50 == 0 or i == total:
            cache.flush()
            enriched_from_cache = sum(1 for d in decisions if d.status == "enriched" and d.cache_hit)
            enriched_live = sum(1 for d in decisions if d.status == "enriched" and not d.cache_hit)
            print(
                f"Progress: {i}/{total} | enriched: {enriched_from_cache + enriched_live} (live: {enriched_live}, cached: {enriched_from_cache})",
                file=sys.stderr,
            )

    cache.flush()

    report = build_report(decisions)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(f"Report written to {args.report}", file=sys.stderr)
    else:
        print(report)

    if args.dry_run:
        print("\n[dry-run] No files written.", file=sys.stderr)
        return

    applied = write_enriched_xml(args.input, args.output, decisions, full_export=args.full_export)
    print(f"Wrote enriched XML to {args.output} ({applied} tracks updated).", file=sys.stderr)


def main() -> None:
    args = _parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
