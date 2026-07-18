from __future__ import annotations

from enricher.models import CandidateMatch, TrackRecord

# Fidelity-contract fixture: a full Rekordbox XML export used to verify that
# write_enriched_xml() touches only blank→filled Label/Year/Remixer + Colour and
# leaves everything else (Comments, unknown attrs, children) byte-identical.
_SOURCE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
 <PRODUCT Name="rekordbox" Version="7.2.16" Company="AlphaTheta"/>
 <COLLECTION Entries="2">
  <TRACK TrackID="1" Name="Keep On" Artist="Denham Audio" Genre="Breakbeat" AverageBpm="140.00"
         Tonality="2A" TotalTime="286" Label="" Year="2022" Remixer=""
         Comments="2A - Energy 6 /* Big Sound / Brooding */" MyCustomAttr="keep-me">
   <TEMPO Inizio="0.05" Bpm="140.00" Metro="4/4" Battito="1"/>
   <POSITION_MARK Name="" Type="0" Start="0.05" Num="0" Red="40" Green="226" Blue="20"/>
  </TRACK>
  <TRACK TrackID="2" Name="Done" Artist="Someone" Genre="House" AverageBpm="126.00" Tonality="7A"
         TotalTime="300" Label="Existing" Year="1999" Comments="7A - Energy 5"/>
 </COLLECTION>
 <PLAYLISTS><NODE Type="0" Name="ROOT" Count="0"/></PLAYLISTS>
</DJ_PLAYLISTS>"""


def _track(**overrides: object) -> TrackRecord:
    base: dict[str, object] = {
        "track_id": "1",
        "name": "Keep On",
        "artist": "Denham Audio",
        "genre": "Breakbeat",
        "bpm": 140.0,
        "tonality": "2A",
        "duration_seconds": 286,
    }
    base.update(overrides)
    return TrackRecord(**base)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> CandidateMatch:
    base: dict[str, object] = {
        "source": "discogs",
        "source_id": "123",
        "artist": "Denham Audio",
        "title": "Keep On",
    }
    base.update(overrides)
    return CandidateMatch(**base)  # type: ignore[arg-type]
