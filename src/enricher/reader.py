from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

from enricher.models import TrackRecord

_SOUNDCLOUD_PREFIX = "file://localhostsoundcloud"
_REKORDBOX_DEMO_ARTIST = "rekordbox"


def parse_collection(xml_path: Path) -> list[TrackRecord]:
    if not xml_path.exists():
        raise FileNotFoundError(f"Rekordbox XML not found: {xml_path}")

    tree = etree.parse(str(xml_path))  # noqa: S320 — local file path, not user-supplied network input
    root = tree.getroot()

    collection = root.find(".//COLLECTION")
    if collection is None:
        raise ValueError("No <COLLECTION> node found in Rekordbox XML.")

    tracks: list[TrackRecord] = []
    excluded = 0

    for element in collection.findall("TRACK"):
        location = element.get("Location", "")
        if location.startswith(_SOUNDCLOUD_PREFIX):
            excluded += 1
            continue

        track_id = element.get("TrackID", "")
        artist = element.get("Artist", "")

        if not artist.strip() or artist.lower() == _REKORDBOX_DEMO_ARTIST:
            excluded += 1
            continue
        name = element.get("Name", "")
        genre = element.get("Genre", "")
        bpm_raw = element.get("AverageBpm", "0")
        tonality = element.get("Tonality", "")
        total_time_raw = element.get("TotalTime", "0")

        tracks.append(
            TrackRecord(
                track_id=track_id,
                name=name,
                artist=artist,
                genre=genre,
                bpm=float(bpm_raw) if bpm_raw else 0.0,
                tonality=tonality,
                label=element.get("Label", ""),
                year=element.get("Year", ""),
                remixer=element.get("Remixer", ""),
                album=element.get("Album", ""),
                mix=element.get("Mix", ""),
                duration_seconds=int(total_time_raw) if total_time_raw.isdigit() else 0,
            )
        )

    print(f"Parsed {len(tracks)} tracks, excluded {excluded} SoundCloud tracks.", file=sys.stderr)
    return tracks
