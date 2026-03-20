from __future__ import annotations

from pathlib import Path

from lxml import etree

from enricher.models import EnrichmentDecision

_PROTECTED_ATTRS = frozenset({"Name", "Artist", "Comments", "Genre", "TotalTime", "AverageBpm", "Tonality"})

_ENRICHABLE_FIELDS: list[tuple[str, str]] = [
    ("label", "Label"),
    ("year", "Year"),
    ("remixer", "Remixer"),
    ("album", "Album"),
    ("mix", "Mix"),
]


def _build_playlists(updated_ids: list[str], unresolved_ids: list[str]) -> etree._Element:
    """Build a PLAYLISTS node with 'Updated Tracks' and optionally 'Unable to Enrich' playlists."""
    playlists = etree.Element("PLAYLISTS")
    count = "2" if unresolved_ids else "1"
    root_node = etree.SubElement(playlists, "NODE", Type="0", Name="ROOT", Count=count)

    updated_node = etree.SubElement(
        root_node,
        "NODE",
        Type="1",
        Name="Updated Tracks",
        KeyType="0",
        Entries=str(len(updated_ids)),
    )
    for track_id in updated_ids:
        etree.SubElement(updated_node, "TRACK", Key=track_id)

    if unresolved_ids:
        unresolved_node = etree.SubElement(
            root_node,
            "NODE",
            Type="1",
            Name="Unable to Enrich",
            KeyType="0",
            Entries=str(len(unresolved_ids)),
        )
        for track_id in unresolved_ids:
            etree.SubElement(unresolved_node, "TRACK", Key=track_id)

    return playlists


_UNRESOLVABLE_STATUSES = frozenset({"skipped_no_match", "skipped_low_confidence", "skipped_api_error"})


def write_enriched_xml(
    source_path: Path,
    output_path: Path,
    decisions: list[EnrichmentDecision],
    dry_run: bool = False,
    full_export: bool = False,
) -> int:
    tree = etree.parse(str(source_path))  # noqa: S320 — local file, not user-supplied network input
    root = tree.getroot()

    decision_map = {d.track_id: d for d in decisions}

    collection = root.find(".//COLLECTION")
    if collection is None:
        raise ValueError("No <COLLECTION> node found in source XML.")

    applied = 0
    to_remove: list[etree._Element] = []

    for element in collection.findall("TRACK"):
        track_id = element.get("TrackID", "")
        decision = decision_map.get(track_id)

        if decision is None:
            to_remove.append(element)
            continue

        # Apply metadata enrichment fields
        if decision.status == "enriched" and decision.match is not None:
            match = decision.match
            for field_name, xml_attr in _ENRICHABLE_FIELDS:
                new_value = getattr(match, field_name, "")
                if new_value and xml_attr not in _PROTECTED_ATTRS:
                    element.set(xml_attr, new_value)

            if decision.confidence_colour is not None:
                element.set("Colour", decision.confidence_colour)

            applied += 1

        # Blank colour for tracks with no usable match in colour-confidence mode
        elif decision.clear_colour:
            element.set("Colour", "")

        else:
            # Not enriched and no colour change — exclude from delta export
            to_remove.append(element)

    for element in to_remove:
        collection.remove(element)

    # Update Entries count to match what's actually in the output
    collection.set("Entries", str(len(collection.findall("TRACK"))))

    # Replace PLAYLISTS with review playlists: updated tracks + unable-to-enrich
    for node in root.findall("PLAYLISTS"):
        root.remove(node)
    # In full_export mode, playlist membership is determined by decision status only —
    # clear_colour tracks stay in the COLLECTION (colour is blanked) but belong in Unable to Enrich,
    # not Updated Tracks. Build both lists from decisions, not from COLLECTION contents.
    if full_export:
        unresolved_ids = [d.track_id for d in decisions if d.status in _UNRESOLVABLE_STATUSES]
        unresolved_id_set = set(unresolved_ids)
        enriched_ids = [d.track_id for d in decisions if d.status == "enriched"]
        already_complete_ids = [d.track_id for d in decisions if d.status == "skipped_already_complete"]
        updated_ids = enriched_ids + already_complete_ids
    else:
        updated_ids = [element.get("TrackID", "") for element in collection.findall("TRACK")]
        updated_id_set = set(updated_ids)
        unresolved_ids = [
            d.track_id for d in decisions
            if d.status == "skipped_no_match" and d.track_id not in updated_id_set
        ]
    root.append(_build_playlists(updated_ids, unresolved_ids))

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
        output_path.write_bytes(xml_bytes)

    return applied
