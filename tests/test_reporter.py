from __future__ import annotations

from enricher.models import CandidateMatch, EnrichmentDecision
from enricher.reporter import build_report


def _enriched(track_id: str = "1", provider: str | None = None) -> EnrichmentDecision:
    return EnrichmentDecision(
        track_id=track_id,
        artist="DJ Example",
        title="Some Track",
        status="enriched",
        match=CandidateMatch(
            source="musicbrainz",
            source_id="x",
            artist="DJ Example",
            title="Some Track",
            label="Defected",
            year="2021",
            confidence=0.9,
        ),
        fields_changed={"label": ("", "Defected")},
        disambiguation_used=provider,  # type: ignore[arg-type]
    )


def _skipped(status: str) -> EnrichmentDecision:
    return EnrichmentDecision(
        track_id="2",
        artist="Other Artist",
        title="Other Track",
        status=status,  # type: ignore[arg-type]
    )


def test_report_summary_counts_are_correct() -> None:
    decisions = [
        _enriched("1"),
        _skipped("skipped_low_confidence"),
        _skipped("skipped_no_match"),
        _skipped("skipped_api_error"),
        _skipped("skipped_already_complete"),
    ]
    report = build_report(decisions)
    assert "Enriched               : 1" in report
    assert "Skipped (low conf.)    : 1" in report
    assert "Skipped (no match)     : 1" in report
    assert "Skipped (API error)    : 1" in report
    assert "Already complete       : 1" in report


def test_report_shows_llm_provider_when_used() -> None:
    decisions = [_enriched(provider="minimax")]
    report = build_report(decisions)
    assert "minimax" in report


def test_report_shows_enriched_fields() -> None:
    decisions = [_enriched()]
    report = build_report(decisions)
    assert "Defected" in report
    assert "label" in report


def test_report_shows_unresolved_section_for_low_confidence() -> None:
    decisions = [
        EnrichmentDecision(
            track_id="3",
            artist="Mystery Artist",
            title="Unknown Track",
            status="skipped_low_confidence",
            match=CandidateMatch(
                source="discogs",
                source_id="y",
                artist="Mystery Artist",
                title="Unknown Track",
                confidence=0.55,
            ),
        )
    ]
    report = build_report(decisions)
    assert "UNRESOLVED" in report
    assert "Mystery Artist" in report


def test_report_empty_decisions_does_not_crash() -> None:
    report = build_report([])
    assert "Total tracks processed : 0" in report
