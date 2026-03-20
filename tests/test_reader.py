from __future__ import annotations

from pathlib import Path

import pytest

from enricher.reader import parse_collection

_MINIMAL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="7.2.11" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="Alpha" Artist="Artist A" Genre="House"
           AverageBpm="125.00" Tonality="4A" TotalTime="300"
           Label="Defected" Year="2022" Remixer="" Album="EP 1" Mix="Original Mix"
           Location="file://localhost/music/alpha.mp3"/>
    <TRACK TrackID="2" Name="Beta" Artist="Artist B" Genre="Techno"
           AverageBpm="140.00" Tonality="10B" TotalTime="420"
           Label="" Year="" Remixer="" Album="" Mix=""
           Location="file://localhost/music/beta.mp3"/>
    <TRACK TrackID="sc1" Name="SoundCloud Track" Artist="SC Artist" Genre="House"
           AverageBpm="126.00" Tonality="5A" TotalTime="200"
           Label="SC" Year="2023" Remixer="" Album="" Mix=""
           Location="file://localhostsoundcloud/some/track"/>
  </COLLECTION>
</DJ_PLAYLISTS>
"""


@pytest.fixture
def xml_file(tmp_path: Path) -> Path:
    p = tmp_path / "rekordbox.xml"
    p.write_text(_MINIMAL_XML, encoding="utf-8")
    return p


def test_parse_collection_returns_non_soundcloud_tracks(xml_file: Path) -> None:
    tracks = parse_collection(xml_file)
    assert len(tracks) == 2
    ids = {t.track_id for t in tracks}
    assert "sc1" not in ids


def test_parse_collection_reads_fields_correctly(xml_file: Path) -> None:
    tracks = parse_collection(xml_file)
    alpha = next(t for t in tracks if t.track_id == "1")
    assert alpha.name == "Alpha"
    assert alpha.artist == "Artist A"
    assert alpha.genre == "House"
    assert alpha.bpm == 125.0
    assert alpha.tonality == "4A"
    assert alpha.duration_seconds == 300
    assert alpha.label == "Defected"
    assert alpha.year == "2022"
    assert alpha.album == "EP 1"
    assert alpha.mix == "Original Mix"


def test_parse_collection_handles_empty_optional_fields(xml_file: Path) -> None:
    tracks = parse_collection(xml_file)
    beta = next(t for t in tracks if t.track_id == "2")
    assert beta.label == ""
    assert beta.year == ""
    assert beta.album == ""


def test_parse_collection_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_collection(tmp_path / "missing.xml")


def test_parse_collection_missing_collection_node_raises(tmp_path: Path) -> None:
    bad_xml = tmp_path / "bad.xml"
    bad_xml.write_text('<?xml version="1.0"?><DJ_PLAYLISTS Version="1.0.0"/>', encoding="utf-8")
    with pytest.raises(ValueError, match="COLLECTION"):
        parse_collection(bad_xml)
