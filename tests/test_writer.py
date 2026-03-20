from __future__ import annotations

from pathlib import Path

from lxml import etree

from enricher.models import CandidateMatch, EnrichmentDecision
from enricher.writer import write_enriched_xml

_SOURCE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="7.2.11" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Alpha" Artist="Artist A" Genre="House"
           AverageBpm="125.00" Tonality="4A" TotalTime="300"
           Label="" Year="" Remixer="" Album="" Mix=""
           Location="file://localhost/music/alpha.mp3"/>
    <TRACK TrackID="2" Name="Beta" Artist="Artist B" Genre="Techno"
           AverageBpm="140.00" Tonality="10B" TotalTime="420"
           Label="" Year="" Remixer="" Album="" Mix=""
           Location="file://localhost/music/beta.mp3"/>
  </COLLECTION>
</DJ_PLAYLISTS>
"""


def _make_enriched_decision(track_id: str) -> EnrichmentDecision:
    return EnrichmentDecision(
        track_id=track_id,
        artist="Artist A",
        title="Alpha",
        status="enriched",
        match=CandidateMatch(
            source="musicbrainz",
            source_id="x",
            artist="Artist A",
            title="Alpha",
            label="Defected",
            year="2022",
            remixer="Remixer X",
            album="Some Album",
            mix="Original Mix",
            confidence=0.9,
        ),
        fields_changed={
            "label": ("", "Defected"),
            "year": ("", "2022"),
        },
        disambiguation_used=None,
    )


def test_write_enriched_xml_applies_enrichment(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [_make_enriched_decision("1")]
    applied = write_enriched_xml(source, output, decisions)

    assert applied == 1
    tree = etree.parse(str(output))
    track1 = tree.find(".//TRACK[@TrackID='1']")
    assert track1 is not None
    assert track1.get("Label") == "Defected"
    assert track1.get("Year") == "2022"
    assert track1.get("Remixer") == "Remixer X"
    assert track1.get("Album") == "Some Album"
    assert track1.get("Mix") == "Original Mix"


def test_write_enriched_xml_does_not_touch_protected_fields(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [_make_enriched_decision("1")]
    write_enriched_xml(source, output, decisions)

    tree = etree.parse(str(output))
    track1 = tree.find(".//TRACK[@TrackID='1']")
    assert track1 is not None
    assert track1.get("Name") == "Alpha"
    assert track1.get("Artist") == "Artist A"
    assert track1.get("Genre") == "House"
    assert track1.get("AverageBpm") == "125.00"
    assert track1.get("Tonality") == "4A"


def test_write_enriched_xml_excludes_unenriched_tracks(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [_make_enriched_decision("1")]
    write_enriched_xml(source, output, decisions)

    tree = etree.parse(str(output))
    # Only enriched track 1 should appear — track 2 was not enriched
    assert tree.find(".//TRACK[@TrackID='1']") is not None
    assert tree.find(".//TRACK[@TrackID='2']") is None
    collection = tree.find(".//COLLECTION")
    assert collection is not None
    assert collection.get("Entries") == "1"


def test_write_enriched_xml_clears_colour_for_no_match_decision(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [
        EnrichmentDecision(
            track_id="2",
            artist="Artist B",
            title="Beta",
            status="skipped_no_match",
            clear_colour=True,
        )
    ]
    write_enriched_xml(source, output, decisions)

    tree = etree.parse(str(output))
    track2 = tree.find(".//TRACK[@TrackID='2']")
    assert track2 is not None
    assert track2.get("Colour") == ""


def test_write_enriched_xml_skipped_no_colour_change_excluded(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    xml_with_colour = _SOURCE_XML.replace('TrackID="2" Name="Beta"', 'TrackID="2" Name="Beta" Colour="0xFF0000"')
    source.write_text(xml_with_colour, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [
        EnrichmentDecision(
            track_id="2",
            artist="Artist B",
            title="Beta",
            status="skipped_no_match",
            clear_colour=False,  # no colour change — track excluded from delta export
        )
    ]
    write_enriched_xml(source, output, decisions)

    tree = etree.parse(str(output))
    # Track was skipped with no colour change — not an update, must not appear
    assert tree.find(".//TRACK[@TrackID='2']") is None


def test_write_enriched_xml_builds_updated_tracks_playlist(tmp_path: Path) -> None:
    xml_with_playlists = _SOURCE_XML.replace(
        "</DJ_PLAYLISTS>",
        '  <PLAYLISTS><NODE Type="0" Name="ROOT"/></PLAYLISTS>\n</DJ_PLAYLISTS>',
    )
    source = tmp_path / "source.xml"
    source.write_text(xml_with_playlists, encoding="utf-8")
    output = tmp_path / "output.xml"

    write_enriched_xml(source, output, [_make_enriched_decision("1")])

    tree = etree.parse(str(output))
    # No unresolved tracks — only "Updated Tracks", ROOT Count="1"
    root_node = tree.find(".//NODE[@Name='ROOT']")
    assert root_node is not None
    assert root_node.get("Count") == "1"
    playlist = tree.find(".//NODE[@Name='Updated Tracks']")
    assert playlist is not None
    assert playlist.get("Entries") == "1"
    assert playlist.find("TRACK[@Key='1']") is not None
    assert tree.find(".//NODE[@Name='Unable to Enrich']") is None


def test_write_enriched_xml_builds_unable_to_enrich_playlist(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [
        _make_enriched_decision("1"),
        EnrichmentDecision(
            track_id="2",
            artist="Artist B",
            title="Beta",
            status="skipped_no_match",
        ),
    ]
    write_enriched_xml(source, output, decisions)

    tree = etree.parse(str(output))
    root_node = tree.find(".//NODE[@Name='ROOT']")
    assert root_node is not None
    assert root_node.get("Count") == "2"
    unresolved = tree.find(".//NODE[@Name='Unable to Enrich']")
    assert unresolved is not None
    assert unresolved.get("Entries") == "1"
    assert unresolved.find("TRACK[@Key='2']") is not None


def test_write_enriched_xml_full_export_every_track_in_exactly_one_playlist(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [
        _make_enriched_decision("1"),
        EnrichmentDecision(
            track_id="2",
            artist="Artist B",
            title="Beta",
            status="skipped_already_complete",
        ),
    ]
    write_enriched_xml(source, output, decisions, full_export=True)
    tree = etree.parse(str(output))

    # Track 1 enriched → Updated Tracks
    updated = tree.find(".//NODE[@Name='Updated Tracks']")
    assert updated is not None
    assert updated.find("TRACK[@Key='1']") is not None

    # Track 2 already_complete → also in Updated Tracks (covered, nothing to fix)
    assert updated.find("TRACK[@Key='2']") is not None

    # Track 2 must also appear in the COLLECTION so Rekordbox can resolve the playlist reference
    assert tree.find(".//TRACK[@TrackID='2']") is not None

    # No Unable to Enrich playlist (nothing unresolved)
    assert tree.find(".//NODE[@Name='Unable to Enrich']") is None


def test_write_enriched_xml_full_export_low_confidence_in_unable_to_enrich(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [
        _make_enriched_decision("1"),
        EnrichmentDecision(
            track_id="2",
            artist="Artist B",
            title="Beta",
            status="skipped_low_confidence",
        ),
    ]
    # Without full_export: skipped_low_confidence silently excluded
    write_enriched_xml(source, output, decisions, full_export=False)
    tree = etree.parse(str(output))
    assert tree.find(".//NODE[@Name='Unable to Enrich']") is None

    # With full_export: skipped_low_confidence appears in Unable to Enrich
    write_enriched_xml(source, output, decisions, full_export=True)
    tree = etree.parse(str(output))
    unresolved = tree.find(".//NODE[@Name='Unable to Enrich']")
    assert unresolved is not None
    assert unresolved.find("TRACK[@Key='2']") is not None


def test_write_enriched_xml_dry_run_does_not_write_file(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_text(_SOURCE_XML, encoding="utf-8")
    output = tmp_path / "output.xml"

    decisions = [_make_enriched_decision("1")]
    write_enriched_xml(source, output, decisions, dry_run=True)

    assert not output.exists()
