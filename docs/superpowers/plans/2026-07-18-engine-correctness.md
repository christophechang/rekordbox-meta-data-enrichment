# Engine Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the enrichment engine precision-first and trustworthy: fill-blank-only writes, candidate-replay cache, Beatport as primary source, MusicBrainz repairs, mix-aware year semantics, honest error handling.

**Architecture:** Linear pipeline unchanged (reader → per-track lookup/score/decide → reporter/writer). Changes: decisions become a pure function of (current track state, cached candidates, config); the cache stores raw candidates, never decisions; a new `beatport.py` source module joins `lookup.py`; the writer whitelists exactly `Label`/`Year`/`Remixer`/`Colour` and only writes fields listed in `fields_changed`.

**Tech Stack:** Python 3.12, httpx (async), lxml, Pydantic v2, pytest + respx + pytest-asyncio (auto mode). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-18-enrich-in-transit-design.md` (§4 engine fixes; §9 invariants). This plan covers spec milestones 1–3. The Mini daemon (spec §5, milestone 4) is a separate follow-up plan.

## Global Constraints

- Python ≥3.12; `mypy --strict` must pass; every module starts with `from __future__ import annotations`.
- Full verification gate (run before every commit): `ruff format . && ruff check . && mypy . && pytest`
- **No new dependencies** — stdlib + existing (`httpx`, `lxml`, `pydantic`, `python-dotenv`; dev: `respx`, `pytest-asyncio`, `pytest-mock`, `lxml-stubs`).
- Conventional commits. Do NOT add `Co-Authored-By` trailers.
- CLI contract additive-only: existing flags keep their names and meanings; `--sources` gains choices but `both` keeps meaning musicbrainz+discogs.
- Status literals unchanged: `enriched`, `skipped_low_confidence`, `skipped_no_match`, `skipped_api_error`, `skipped_already_complete`.
- Invariants (spec §9): only `Label`, `Year`, `Remixer`, `Colour` XML attributes are ever written; `Name`, `Artist`, `Comments` never modified; never replace a non-empty field; empty candidate sets and API errors are never cached.
- Tests mock all HTTP with respx; no live API calls in the default suite.
- All paths below are relative to the repo root. Source: `src/enricher/`. Tests: `tests/`.

---

### Task 1: Fill-blank-only decision policy

**Files:**
- Modify: `src/enricher/enricher.py` (`_fields_changed` at :77-89, delete `_apply_styles` at :57-74, call sites :179 and :210)
- Test: `tests/test_enricher.py`

**Interfaces:**
- Produces: `_fields_changed(track: TrackRecord, match: CandidateMatch) -> dict[str, tuple[str, str]]` — keys limited to `"label" | "year" | "remixer"`; an entry exists only when the track's field is empty (`not old`) and the match has a value. Task 2's writer and Task 11's `build_changes` rely on `fields_changed` being exactly the set of intended writes.
- Removes: `_apply_styles` (Mix decoration retired — spec limits written fields to Label/Year/Remixer/Colour). `filter_styles_by_bpm` stays in `scorer.py` (still unit-tested; future report-only mood suggestions use it).

- [ ] **Step 1: Write the failing tests**

Create `tests/factories.py` — shared builders every later task imports (Step 3 adds `tests` to pytest's pythonpath so `from factories import _track` resolves):

```python
from __future__ import annotations

from enricher.models import CandidateMatch, TrackRecord


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
```

Add to `tests/test_enricher.py`:

```python
from factories import _candidate, _track

from enricher.enricher import _fields_changed


def test_fields_changed_fills_blank_fields_only() -> None:
    track = _track(label="", year="2022", remixer="")
    match = _candidate(label="Club Glow", year="2019", remixer="Someone")
    changes = _fields_changed(track, match)
    assert changes == {"label": ("", "Club Glow"), "remixer": ("", "Someone")}
    # year is non-empty on the track: never replaced, even though the match disagrees


def test_fields_changed_never_proposes_album_or_mix() -> None:
    track = _track(label="", album="", mix="")
    match = _candidate(label="Club Glow", album="Some Album", mix="Club Mix")
    changes = _fields_changed(track, match)
    assert set(changes) == {"label"}


def test_fields_changed_empty_when_track_complete() -> None:
    track = _track(label="Hotflush", year="2015", remixer="")
    match = _candidate(label="Other", year="1999", remixer="")
    assert _fields_changed(track, match) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_enricher.py -k fields_changed -v`
Expected: FAIL — `test_fields_changed_fills_blank_fields_only` fails because current code proposes `year: ("2022", "2019")`; `never_proposes_album_or_mix` fails because album/mix are proposed.

- [ ] **Step 3: Implement**

In `src/enricher/enricher.py` replace `_fields_changed` (:77-89):

```python
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
```

Delete `_apply_styles` (:57-74) and the `filter_styles_by_bpm` import (:9 — keep `score_all`). Update the two call sites:

- Line 179: `best = _apply_styles(track, _fill_label(scored[0], candidates), candidates)` → `best = _fill_label(scored[0], candidates)`
- Line 210: `chosen = _apply_styles(track, _fill_label(ambiguous[chosen_idx], candidates), candidates)` → `chosen = _fill_label(ambiguous[chosen_idx], candidates)`

Delete any existing tests that assert `_apply_styles` behaviour or year-replacement behaviour (search `tests/` for `_apply_styles` and fix assertions that expect non-blank fields to be replaced).

In `pyproject.toml`, extend the pytest pythonpath so `tests/factories.py` is importable:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["src", "tests"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/enricher/enricher.py tests/test_enricher.py
git commit -m "feat: fill-blank-only enrichment policy, retire album/mix writes"
```

---

### Task 2: Writer — field whitelist, fields_changed gating, fidelity contract

**Files:**
- Modify: `src/enricher/writer.py` (`_ENRICHABLE_FIELDS` :11-17, apply block :83-93, full_export branch :95-105)
- Test: `tests/test_writer.py`

**Interfaces:**
- Consumes: `decision.fields_changed` from Task 1 (authoritative set of writes).
- Produces: the fidelity contract every later task (and the daemon plan) relies on — output diff vs source = blank→filled `Label`/`Year`/`Remixer` + `Colour` changes, nothing else. In `full_export` mode no decision-holding track is removed (fixes dangling playlist refs).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_writer.py`:

```python
from pathlib import Path

from lxml import etree

from enricher.models import CandidateMatch, EnrichmentDecision
from enricher.writer import write_enriched_xml

# _SOURCE_XML lives in tests/factories.py (test_main.py reuses it in Task 10):
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


def _decision_enrich_track1() -> EnrichmentDecision:
    match = CandidateMatch(
        source="discogs", source_id="9", artist="Denham Audio", title="Keep On",
        label="Club Glow", year="2019", remixer="", album="Should Never Land", mix="Club Mix",
    )
    return EnrichmentDecision(
        track_id="1", artist="Denham Audio", title="Keep On", status="enriched",
        match=match, fields_changed={"label": ("", "Club Glow")}, confidence_colour="0x00FF00",
    )


def test_writer_fidelity_contract(tmp_path: Path) -> None:
    src = tmp_path / "src.xml"
    out = tmp_path / "out.xml"
    src.write_text(_SOURCE_XML, encoding="utf-8")
    complete = EnrichmentDecision(track_id="2", artist="Someone", title="Done", status="skipped_already_complete")

    write_enriched_xml(src, out, [_decision_enrich_track1(), complete], full_export=True)

    root = etree.parse(str(out)).getroot()
    t1 = root.find(".//TRACK[@TrackID='1']")
    t2 = root.find(".//TRACK[@TrackID='2']")
    assert t1 is not None and t2 is not None
    # Only the blank Label was filled + Colour set:
    assert t1.get("Label") == "Club Glow"
    assert t1.get("Colour") == "0x00FF00"
    # Year non-blank in source: untouched even though fields_changed lacks it and match disagrees
    assert t1.get("Year") == "2022"
    # Album/Mix from the match must never land:
    assert t1.get("Album") is None
    assert t1.get("Mix") is None
    # Comments and unknown attrs byte-identical:
    assert t1.get("Comments") == "2A - Energy 6 /* Big Sound / Brooding */"
    assert t1.get("MyCustomAttr") == "keep-me"
    # Children (beatgrid, cues) survive exactly:
    src_t1 = etree.parse(str(src)).getroot().find(".//TRACK[@TrackID='1']")
    assert src_t1 is not None
    assert [etree.tostring(c) for c in t1] == [etree.tostring(c) for c in src_t1]
    # Already-complete track untouched entirely in full_export:
    assert t2.get("Label") == "Existing" and t2.get("Year") == "1999"


def test_writer_never_writes_outside_whitelist(tmp_path: Path) -> None:
    src = tmp_path / "src.xml"
    out = tmp_path / "out.xml"
    src.write_text(_SOURCE_XML, encoding="utf-8")
    d = _decision_enrich_track1()
    d.fields_changed["album"] = ("", "Sneaky")  # hostile fields_changed entry
    write_enriched_xml(src, out, [d], full_export=False)
    t1 = etree.parse(str(out)).getroot().find(".//TRACK[@TrackID='1']")
    assert t1 is not None and t1.get("Album") is None


def test_full_export_keeps_unresolved_tracks(tmp_path: Path) -> None:
    src = tmp_path / "src.xml"
    out = tmp_path / "out.xml"
    src.write_text(_SOURCE_XML, encoding="utf-8")
    unresolved = EnrichmentDecision(track_id="1", artist="Denham Audio", title="Keep On", status="skipped_api_error")
    complete = EnrichmentDecision(track_id="2", artist="Someone", title="Done", status="skipped_already_complete")
    write_enriched_xml(src, out, [unresolved, complete], full_export=True)
    root = etree.parse(str(out)).getroot()
    # api-error track stays in COLLECTION so the Unable to Enrich playlist reference resolves:
    assert root.find(".//TRACK[@TrackID='1']") is not None
    key_refs = [t.get("Key") for t in root.findall(".//PLAYLISTS//TRACK")]
    collection_ids = {t.get("TrackID") for t in root.findall(".//COLLECTION/TRACK")}
    assert set(key_refs) <= collection_ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_writer.py -k "fidelity or whitelist or keeps_unresolved" -v`
Expected: FAIL — current writer sets every truthy match field (Album/Mix land, Year replaced is prevented only by Task 1 upstream, hostile fields_changed lands), and full_export removes api-error tracks.

- [ ] **Step 3: Implement**

In `src/enricher/writer.py`:

```python
_ENRICHABLE_FIELDS: list[tuple[str, str]] = [
    ("label", "Label"),
    ("year", "Year"),
    ("remixer", "Remixer"),
]
```

Replace the apply block (currently :83-93) inside the `for element in collection.findall("TRACK")` loop:

```python
        # Apply metadata enrichment fields — whitelist × fields_changed × still-blank triple guard
        if decision.status == "enriched" and decision.match is not None:
            for field_name, xml_attr in _ENRICHABLE_FIELDS:
                change = decision.fields_changed.get(field_name)
                if change is not None and not element.get(xml_attr, ""):
                    element.set(xml_attr, change[1])

            if decision.confidence_colour is not None:
                element.set("Colour", decision.confidence_colour)

            applied += 1

        # In full_export mode every decision-holding track is kept so playlist refs resolve
        elif full_export:
            if decision.clear_colour:
                element.set("Colour", "")

        # Blank colour for tracks with no usable match in colour-confidence mode
        elif decision.clear_colour:
            element.set("Colour", "")

        else:
            # Not enriched and no colour change — exclude from delta export
            to_remove.append(element)
```

`_PROTECTED_ATTRS` (:9) stays as documentation; the whitelist now enforces it structurally.

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green (update any existing writer tests that asserted Album/Mix writes or full_export removals).

- [ ] **Step 5: Commit**

```bash
git add src/enricher/writer.py tests/test_writer.py
git commit -m "feat: writer whitelist + fields_changed gating + fidelity contract test"
```

---

### Task 3: Cache v2 — candidates, not decisions

**Files:**
- Modify: `src/enricher/cache.py` (whole file), `src/enricher/enricher.py` (`process_track` :96-260), `src/enricher/__main__.py` (no signature change — verify only)
- Test: `tests/test_cache.py`, `tests/test_enricher.py`

**Interfaces:**
- Produces (all later tasks + daemon rely on these):
  - `CacheProtocol.get(artist: str, title: str) -> list[CandidateMatch] | None`
  - `CacheProtocol.put(artist: str, title: str, candidates: list[CandidateMatch]) -> None` (silently ignores empty lists)
  - `CacheProtocol.flush() -> None` (atomic tmp+rename)
  - Cache file schema v2: `{"version": 2, "entries": {key: {"looked_up_at": iso8601, "candidates": [CandidateMatch dumps]}}}`
  - v1 files are migrated on load: candidate lists salvaged, stored decisions discarded, entries with empty candidates dropped.
- `process_track` flow becomes: completeness check → cache read → (live lookups + `cache.put` only on non-empty candidates) → score → policy. Decisions are never stored.

- [ ] **Step 1: Write the failing tests**

Replace decision-centric tests in `tests/test_cache.py` with:

```python
import json
from pathlib import Path

from enricher.cache import EnrichmentCache
from enricher.models import CandidateMatch


def _cand(label: str = "Hotflush") -> CandidateMatch:
    return CandidateMatch(source="discogs", source_id="1", artist="A", title="T", label=label)


def test_cache_stores_and_returns_candidates(tmp_path: Path) -> None:
    cache = EnrichmentCache(tmp_path / "c.json")
    cache.put("A", "T", [_cand()])
    got = cache.get("A", "T")
    assert got is not None and got[0].label == "Hotflush"


def test_cache_ignores_empty_candidate_lists(tmp_path: Path) -> None:
    # no_match retry semantics live here: an empty result is never persisted
    cache = EnrichmentCache(tmp_path / "c.json")
    cache.put("A", "T", [])
    assert cache.get("A", "T") is None


def test_cache_flush_is_atomic_and_versioned(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    cache = EnrichmentCache(path)
    cache.put("A", "T", [_cand()])
    cache.flush()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2 and "entries" in raw
    assert not path.with_suffix(".tmp").exists()


def test_cache_migrates_v1_salvaging_candidates_dropping_decisions(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    v1 = {
        "a — t": {
            "looked_up_at": "2026-01-01T00:00:00+00:00",
            "candidates": [_cand().model_dump()],
            "decision": {"track_id": "99", "artist": "A", "title": "T", "status": "enriched"},
        },
        "b — u": {"looked_up_at": "2026-01-01T00:00:00+00:00", "candidates": [], "decision": {"track_id": "1", "artist": "B", "title": "U", "status": "skipped_already_complete"}},
    }
    path.write_text(json.dumps(v1), encoding="utf-8")
    cache = EnrichmentCache(path)
    assert cache.get("A", "T") is not None  # candidates salvaged
    assert cache.get("B", "U") is None  # empty-candidates entry dropped


def test_cache_recovers_from_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    cache = EnrichmentCache(path)  # must not raise
    assert cache.get("A", "T") is None
    corrupt_backups = list(tmp_path.glob("c.corrupt-*"))
    assert len(corrupt_backups) == 1
```

Add to `tests/test_enricher.py` (uses the existing respx patterns — mock both source endpoints returning nothing so no live scoring interferes):

```python
import pytest

from enricher.cache import NullCache
from enricher.enricher import process_track
from enricher.models import EnrichmentDecision


class _SpyCache(NullCache):
    def __init__(self, candidates: list[CandidateMatch] | None = None) -> None:
        self.candidates = candidates
        self.put_calls: list[tuple[str, str, int]] = []

    def get(self, artist: str, title: str) -> list[CandidateMatch] | None:
        return self.candidates

    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None:
        self.put_calls.append((artist, title, len(candidates)))


async def test_completeness_check_precedes_cache() -> None:
    # A track completed since the last run must short-circuit BEFORE any cache read,
    # so stale cached candidates can never re-clobber manual fixes.
    cache = _SpyCache(candidates=[_candidate(label="Wrong Label", year="1990")])
    track = _track(label="Correct Label", year="2001")
    decision = await process_track(track, cache=cache, sources="both", use_llm=False)
    assert decision.status == "skipped_already_complete"
    assert cache.put_calls == []


async def test_cached_candidates_replayed_with_current_track_id() -> None:
    cand = _candidate(label="Club Glow", year="2019", artist="Denham Audio", title="Keep On")
    cache = _SpyCache(candidates=[cand])
    track = _track(track_id="fresh-42", label="", year="")
    decision = await process_track(track, cache=cache, sources="both", use_llm=False, colour_confidence=True)
    assert decision.track_id == "fresh-42"  # never the id from a previous run
    assert decision.cache_hit is True
    assert decision.status == "enriched"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py tests/test_enricher.py -v`
Expected: FAIL — `get`/`put` signatures mismatch (TypeError), completeness runs after cache, migration/corruption unhandled.

- [ ] **Step 3: Implement**

`src/enricher/cache.py` — full replacement:

```python
from __future__ import annotations

import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from enricher.models import CandidateMatch


class CacheProtocol(Protocol):
    def get(self, artist: str, title: str) -> list[CandidateMatch] | None: ...
    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None: ...
    def flush(self) -> None: ...


_SAVE_INTERVAL = 50  # write to disk every N puts


class NullCache:
    """Drop-in replacement for EnrichmentCache that never reads or writes anything."""

    def get(self, artist: str, title: str) -> list[CandidateMatch] | None:
        return None

    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None:
        pass

    def flush(self) -> None:
        pass


def _normalise_key(artist: str, title: str) -> str:
    raw = f"{artist} — {title}".lower()
    return unicodedata.normalize("NFC", raw)


class EnrichmentCache:
    """Caches raw lookup candidates only — never decisions.

    Decisions are recomputed every run from (current track state, candidates, config),
    so completeness, flags, and track ids are always fresh. Empty candidate lists are
    never stored (no_match retries on every run — load-bearing, see AGENTS.md).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, Any] = {}
        self._dirty_count = 0
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
            backup = path.with_suffix(f".corrupt-{stamp}")
            path.rename(backup)
            print(f"WARNING: cache file corrupt, moved to {backup}; starting fresh", file=sys.stderr)
            return
        if isinstance(raw, dict) and raw.get("version") == 2:
            entries = raw.get("entries", {})
            self._entries = entries if isinstance(entries, dict) else {}
        elif isinstance(raw, dict):
            # v1 migration: salvage candidates, discard stored decisions
            for key, entry in raw.items():
                if isinstance(entry, dict) and entry.get("candidates"):
                    self._entries[key] = {
                        "looked_up_at": entry.get("looked_up_at", ""),
                        "candidates": entry["candidates"],
                    }
            self._dirty_count = 1  # persist the migrated shape on next flush

    def get(self, artist: str, title: str) -> list[CandidateMatch] | None:
        entry = self._entries.get(_normalise_key(artist, title))
        if entry is None:
            return None
        return [CandidateMatch(**c) for c in entry["candidates"]]

    def put(self, artist: str, title: str, candidates: list[CandidateMatch]) -> None:
        if not candidates:
            return
        self._entries[_normalise_key(artist, title)] = {
            "looked_up_at": datetime.now(tz=timezone.utc).isoformat(),
            "candidates": [c.model_dump() for c in candidates],
        }
        self._dirty_count += 1
        if self._dirty_count >= _SAVE_INTERVAL:
            self.flush()

    def flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump({"version": 2, "entries": self._entries}, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)
        self._dirty_count = 0
```

`src/enricher/enricher.py` — restructure the head of `process_track` (:105-143) to:

```python
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
```

Then: every subsequent `cache.put(track.artist, track.name, scored, decision)` call is **deleted** (lines currently at :117, :176, :202, :231, :247, :259), and every `EnrichmentDecision(...)` constructed after this point gains `cache_hit=cache_hit`. The `if not scored:` heuristic-label/no-match block and the threshold ladder are otherwise unchanged.

(Task 8 replaces the source-ordering block again when Beatport arrives; keep this task's version minimal.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green. Existing tests that asserted decisions-in-cache must be updated to the new semantics (they are the tests replaced in Step 1).

- [ ] **Step 5: Commit**

```bash
git add src/enricher/cache.py src/enricher/enricher.py tests/test_cache.py tests/test_enricher.py
git commit -m "feat: cache candidates not decisions; completeness precedes cache; atomic flush"
```

---

### Task 4: MusicBrainz search repairs

**Files:**
- Modify: `src/enricher/lookup.py` (`_MB_DELAY` :19-20, `_mb_query` :190-207, `_extract_mb_candidates` :119-187)
- Test: `tests/test_lookup.py`

**Interfaces:**
- Produces:
  - `SourceLookupError(Exception)` with attributes `source: str`, message `"{source}: {detail}"` — raised by every lookup function on HTTP failure. Tasks 5, 8, 10 consume it.
  - `_escape_lucene(text: str) -> str`.
  - MB search candidates now carry: `year` from recording-level `first-release-date` (the original-release year), `album` from release stubs, `label=""`, `remixer=""` (search responses never contain `label-info`/`relations` — Task 5 fetches those).
  - `_MB_DELAY = 1.1` (MusicBrainz policy: 1 req/s).

- [ ] **Step 1: Write the failing tests**

In `tests/test_lookup.py`, replace the MB mock payloads with the realistic search shape and add:

```python
import httpx
import pytest
import respx

from enricher.lookup import SourceLookupError, _escape_lucene, lookup_musicbrainz

_MB_SEARCH_RESPONSE = {
    "recordings": [
        {
            "id": "mbid-1",
            "title": "Ladbroke Grove",
            "length": 372000,
            "first-release-date": "1997-06-02",
            "artist-credit": [{"artist": {"name": "Kerri Chandler"}}],
            # Realistic: release stubs have title/date/release-group but NO label-info, NO relations
            "releases": [
                {"title": "Hemisphere", "date": "1997-06-02", "release-group": {"secondary-types": []}},
            ],
        }
    ]
}


@respx.mock
async def test_mb_year_comes_from_first_release_date() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(json=_MB_SEARCH_RESPONSE)
    track = _track(name="Ladbroke Grove", artist="Kerri Chandler")
    results = await lookup_musicbrainz(track)
    assert results[0].year == "1997"
    assert results[0].label == ""  # search cannot supply labels — detail lookup does (Task 5)
    assert results[0].remixer == ""


@respx.mock
async def test_mb_search_sends_no_inc_param_and_escapes_lucene() -> None:
    route = respx.get("https://musicbrainz.org/ws/2/recording/").respond(json={"recordings": []})
    track = _track(name='Who"s Afraid (2:1)', artist="John Tejada")
    await lookup_musicbrainz(track)
    sent = route.calls[0].request.url
    assert "inc=" not in str(sent)
    assert '\\"' in httpx.URL(sent).params["query"] or "\\:" in httpx.URL(sent).params["query"]


@respx.mock
async def test_mb_http_error_raises_source_lookup_error() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(status_code=400)
    with pytest.raises(SourceLookupError) as exc_info:
        await lookup_musicbrainz(_track(name="X", artist="Y"))
    assert exc_info.value.source == "musicbrainz"


@respx.mock
async def test_mb_503_retries_then_succeeds() -> None:
    route = respx.get("https://musicbrainz.org/ws/2/recording/")
    route.side_effect = [
        httpx.Response(503, headers={"Retry-After": "0"}),
        httpx.Response(200, json=_MB_SEARCH_RESPONSE),
    ]
    results = await lookup_musicbrainz(_track(name="Ladbroke Grove", artist="Kerri Chandler"))
    assert results and results[0].year == "1997"


@respx.mock
async def test_mb_fallback_ladder_fires_in_order() -> None:
    # Spec §7: attempts 1→2→3 with (full artist, clean title) → (primary artist, clean title)
    # → (primary artist, designator-stripped title), each firing only on empty results.
    route = respx.get("https://musicbrainz.org/ws/2/recording/").respond(json={"recordings": []})
    track = _track(name="Fall Down (Calibre Remix)", artist="Roni Size & Krust")
    await lookup_musicbrainz(track)
    queries = [httpx.URL(c.request.url).params["query"] for c in route.calls]
    assert len(queries) == 3
    assert "Krust" in queries[0]
    assert "Krust" not in queries[1] and "Roni Size" in queries[1]
    assert "Calibre" not in queries[2] and "Fall Down" in queries[2]


def test_escape_lucene() -> None:
    assert _escape_lucene('Who"s Afraid') == 'Who\\"s Afraid'
    assert _escape_lucene("a+b(c)") == "a\\+b\\(c\\)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_lookup.py -v`
Expected: FAIL — `SourceLookupError`/`_escape_lucene` undefined; `inc=` still sent; year comes from release date; 400/503 swallowed to `[]`. Legacy MB tests using `label-info`/`relations` in search mocks now assert the wrong shape — update them in this task (labels/remixer assertions move to Task 5's detail tests).

- [ ] **Step 3: Implement**

In `src/enricher/lookup.py`:

```python
class SourceLookupError(Exception):
    """A metadata source failed at the HTTP level. Never cached; surfaces as skipped_api_error."""

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        super().__init__(f"{source}: {detail}")


# MusicBrainz policy: max 1 request/second per client. Stay under it.
_MB_DELAY = 1.1
_MB_RETRIES = 3

_LUCENE_SPECIALS = re.compile(r'(&&|\|\||[+\-!(){}\[\]^"~*?:\\/])')


def _escape_lucene(text: str) -> str:
    return _LUCENE_SPECIALS.sub(r"\\\1", text)
```

Replace `_mb_query`:

```python
async def _mb_query(artist: str, title: str) -> list[CandidateMatch]:
    query = f'artist:"{_escape_lucene(artist)}" AND recording:"{_escape_lucene(title)}"'
    params = {"query": query, "fmt": "json", "limit": str(_MAX_CANDIDATES)}
    async with _get_mb_semaphore():
        for attempt in range(_MB_RETRIES):
            await asyncio.sleep(_MB_DELAY)
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(f"{_MB_BASE}/recording/", params=params, headers=_mb_headers())
                if resp.status_code == 503:
                    await asyncio.sleep(float(resp.headers.get("Retry-After", 2**attempt)))
                    continue
                resp.raise_for_status()
                data: dict[str, object] = resp.json()
                return _extract_mb_candidates(data)
            except httpx.HTTPError as exc:
                raise SourceLookupError("musicbrainz", str(exc)) from exc
    raise SourceLookupError("musicbrainz", f"still rate-limited after {_MB_RETRIES} attempts")
```

In `_extract_mb_candidates`: delete the label extraction (:143, :152-158) and remixer extraction (:160-171) blocks (search responses never carry them — this was dead code in production); year becomes:

```python
        year = str(rec.get("first-release-date", "") or "")[:4]
```

`album` extraction via `_best_mb_release` stays (release stubs do carry titles/dates); the candidate is constructed with `label=""`, `remixer=""`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/enricher/lookup.py tests/test_lookup.py
git commit -m "fix: MB search — first-release-date year, drop dead inc param, honest rate limit, surface errors"
```

---

### Task 5: MusicBrainz recording-detail follow-up (label/remixer)

**Files:**
- Modify: `src/enricher/lookup.py` (new function), `src/enricher/enricher.py` (live-lookup branch from Task 3)
- Test: `tests/test_lookup.py`, `tests/test_enricher.py`

**Interfaces:**
- Produces: `mb_recording_details(mbid: str) -> tuple[str, str]` returning `(label, remixer)` (`""` when absent). Raises `SourceLookupError` on HTTP failure.
- `process_track` calls it at most once per track, only in the live-lookup branch, only when the winning candidate is MusicBrainz-sourced and a needed field is missing; the result is merged into the candidate list **before** `cache.put`, so cached candidates always carry the merged values.

- [ ] **Step 1: Write the failing tests**

```python
_MB_DETAIL_RESPONSE = {
    "id": "mbid-1",
    "title": "Ladbroke Grove",
    "releases": [
        {
            "title": "Hemisphere",
            "date": "1997-06-02",
            "release-group": {"secondary-types": []},
            "label-info": [{"label": {"name": "Shelter Records"}}],
        }
    ],
    "relations": [{"type": "remixer", "artist": {"name": "DJ Deep"}}],
}


@respx.mock
async def test_mb_recording_details_extracts_label_and_remixer() -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/mbid-1").respond(json=_MB_DETAIL_RESPONSE)
    label, remixer = await mb_recording_details("mbid-1")
    assert label == "Shelter Records"
    assert remixer == "DJ Deep"


@respx.mock
async def test_detail_lookup_only_fires_when_needed(tmp_path: Path) -> None:
    # Winning MB candidate already satisfies the track's blank fields → no detail call.
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(json=_MB_SEARCH_RESPONSE)
    detail_route = respx.get("https://musicbrainz.org/ws/2/recording/mbid-1").respond(json=_MB_DETAIL_RESPONSE)
    track = _track(name="Ladbroke Grove", artist="Kerri Chandler", label="Already Set", year="", remixer="Set Too")
    cache = EnrichmentCache(tmp_path / "c.json")
    await process_track(track, cache=cache, sources="musicbrainz", use_llm=False, colour_confidence=True)
    assert not detail_route.called
```

And the merge-before-cache pin:

```python
@respx.mock
async def test_detail_result_is_cached_with_candidates(tmp_path: Path) -> None:
    respx.get("https://musicbrainz.org/ws/2/recording/").respond(json=_MB_SEARCH_RESPONSE)
    respx.get("https://musicbrainz.org/ws/2/recording/mbid-1").respond(json=_MB_DETAIL_RESPONSE)
    track = _track(name="Ladbroke Grove", artist="Kerri Chandler", label="", year="")
    cache = EnrichmentCache(tmp_path / "c.json")
    await process_track(track, cache=cache, sources="musicbrainz", use_llm=False, colour_confidence=True)
    cached = cache.get("Kerri Chandler", "Ladbroke Grove")
    assert cached is not None and cached[0].label == "Shelter Records"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_lookup.py tests/test_enricher.py -k detail -v`
Expected: FAIL — `mb_recording_details` undefined.

- [ ] **Step 3: Implement**

In `src/enricher/lookup.py` (the label-info/remixer extraction deleted in Task 4 returns here, against the detail response where those fields actually exist):

```python
async def mb_recording_details(mbid: str) -> tuple[str, str]:
    """Fetch (label, remixer) via the recording lookup endpoint.

    Search responses never include label-info or relations; only this endpoint does.
    NOTE: inc values are '+'-separated and must not be URL-encoded to %2B — build the URL directly.
    """
    url = f"{_MB_BASE}/recording/{mbid}?inc=releases+labels+artist-rels&fmt=json"
    async with _get_mb_semaphore():
        await asyncio.sleep(_MB_DELAY)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, headers=_mb_headers())
                resp.raise_for_status()
                data: dict[str, object] = resp.json()
        except httpx.HTTPError as exc:
            raise SourceLookupError("musicbrainz", str(exc)) from exc

    label = ""
    releases = data.get("releases", [])
    best = _best_mb_release(releases if isinstance(releases, list) else [])
    if best is not None:
        label_info = best.get("label-info", [])
        if isinstance(label_info, list) and label_info:
            first = label_info[0]
            if isinstance(first, dict):
                label_obj = first.get("label", {})
                if isinstance(label_obj, dict):
                    label = str(label_obj.get("name", ""))

    remixer = ""
    relations = data.get("relations", [])
    if isinstance(relations, list):
        for rel in relations:
            if isinstance(rel, dict) and str(rel.get("type", "")).lower() == "remixer":
                artist_obj = rel.get("artist", {})
                if isinstance(artist_obj, dict):
                    remixer = str(artist_obj.get("name", ""))
                    break
    return label, remixer
```

In `src/enricher/enricher.py`, inside the live-lookup `try` from Task 3, after the Discogs conditional and before `cache.put`:

```python
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
```

(`mb_recording_details` joins the imports from `enricher.lookup`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/enricher/lookup.py src/enricher/enricher.py tests/test_lookup.py tests/test_enricher.py
git commit -m "feat: MB recording-detail follow-up restores label/remixer via lookup endpoint"
```

---

### Task 6: Matching fixes — remix regexes, trailing-number guard, Discogs album, containment guard, genre families

**Files:**
- Modify: `src/enricher/lookup.py` (`_TRAILING_BPM_RE` :43, `_MIX_DESIGNATOR_RE` :49-57, Discogs album :258), `src/enricher/scorer.py` (`_REMIX_RE` :9-14, `_artist_score` :121-137, `_GENRE_FAMILIES` :100-108)
- Test: `tests/test_lookup.py`, `tests/test_scorer.py`

**Interfaces:**
- Consumes/produces nothing new — behavioural fixes behind existing signatures.

- [ ] **Step 1: Write the failing tests**

`tests/test_lookup.py` (extend the existing parametrized helpers):

```python
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Move Your Body (Shadow Child Extended Remix)", "Move Your Body"),
        ("Fall Down (Calibre Remix)", "Fall Down"),
        ("Track (Somebody's Flip)", "Track"),
    ],
)
def test_strip_mix_designators_handles_artist_remix(title: str, expected: str) -> None:
    assert _strip_mix_designators(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Keep On 7A", "Keep On"),
        ("Keep On 7A 128", "Keep On"),
        ("Keep On 128 7A", "Keep On"),
        ("Xpander 2", "Xpander 2"),  # bare numbers are legitimate title content
        ("Vol. 3", "Vol. 3"),
    ],
)
def test_trailing_bpm_only_strips_mik_artefacts(title: str, expected: str) -> None:
    assert _clean_title(title) == expected


def test_discogs_album_is_release_title_not_artist_prefixed() -> None:
    data = {"results": [{"id": 1, "title": "Kerri Chandler - Hemisphere", "year": 1997, "label": ["Shelter"]}]}
    cands = _extract_discogs_candidates(data, "Ladbroke Grove")
    assert cands[0].album == "Hemisphere"
    assert cands[0].artist == "Kerri Chandler"
```

`tests/test_scorer.py`:

```python
def test_remix_re_strips_artist_remix_suffix() -> None:
    assert _strip_remix("Fall Down (Calibre Remix)") == "Fall Down"
    assert _strip_remix("Move Your Body (Shadow Child Extended Remix)") == "Move Your Body"


def test_artist_containment_requires_min_length() -> None:
    # "Ben" ⊂ "Benny Benassi" must NOT score as containment
    assert _artist_score("Ben", "Benny Benassi") < 0.35
    # Legitimate containment still works
    assert _artist_score("Dusky", "Dusky feat. Solomon Grey") == 0.35


def test_genre_bonus_covers_dubstep_and_disco() -> None:
    assert _genre_bonus("Dubstep", "discogs") == 0.05
    assert _genre_bonus("Nu-Disco", "discogs") == 0.05
    assert _genre_bonus("Hip Hop", "discogs") == 0.05
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_lookup.py tests/test_scorer.py -v`
Expected: FAIL on every new case (Calibre Remix unstripped, "Xpander 2" mangled to "Xpander", album artist-prefixed, "Ben" containment 0.35, dubstep bonus 0.0).

- [ ] **Step 3: Implement**

`src/enricher/lookup.py`:

```python
# MIK artefacts only: trailing "7A", "7A 128", or "128 7A". Bare numbers are never
# stripped — "Xpander 2" and "Vol. 3" are legitimate titles.
_TRAILING_BPM_RE = re.compile(r"\s+(?:\d{1,2}[AB](?:\s+\d{2,3})?|\d{2,3}\s+\d{1,2}[AB])\s*$", re.IGNORECASE)

# Matches common mix/version/feat designators that DBs often omit from track listings,
# including the dominant club pattern "(<Artist> Remix)".
_MIX_DESIGNATOR_RE = re.compile(
    r"\s*[\[\(]"
    r"(?:Original Mix|Extended Mix|Extended|Club Mix|VIP Mix|Dub Mix|Dub|Instrumental|"
    r"Radio Edit|Radio Mix|Album Version|Single Version|\d{4}\s+Remaster(?:ed)?|Remaster(?:ed)?|"
    r"feat\.[^)\]]*|ft\.[^)\]]*|with\s+[^)\]]*|Featuring\s+[^)\]]*|"
    r"[^)\]]+\s+(?:presents|pres\.?)\s+[^)\]]*|"
    r"[^)\]]+(?:'s)?\s+(?:Remix|Mix|Edit|Rework|Refix|Bootleg|VIP|Flip)|"
    r"Remix)"
    r"[\]\)]\s*",
    re.IGNORECASE,
)
```

Discogs extraction (:258): `album=str(result.get("title", ""))` → `album=title` (the post-split release title; when there is no `" - "` separator, `title` already holds the raw release title).

`src/enricher/scorer.py`:

```python
_REMIX_RE = re.compile(
    r"\s*[\(\[][^\)\]]*"
    r"(?:original|club|radio|extended|instrumental|dub|vocal|mix|edit|version|vip|reprise|"
    r"bootleg|rework|refix|remix|remaster(?:ed)?|flip)"
    r"[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
```

`_artist_score` containment branch:

```python
    if (norm_track in norm_cand or norm_cand in norm_track) and min(len(norm_track), len(norm_cand)) >= 4:
        return 0.35
```

`_GENRE_FAMILIES` — append:

```python
    frozenset({"dubstep", "grime", "uk bass", "bass", "bass music", "140"}),
    frozenset({"disco", "nu-disco", "nu disco", "funk", "boogie"}),
    frozenset({"ambient", "downtempo", "trip hop", "trip-hop", "chillout"}),
    frozenset({"hip hop", "hip-hop", "rap", "trap"}),
    frozenset({"dub", "reggae", "dancehall"}),
    frozenset({"hardcore", "happy hardcore", "rave", "gabber"}),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green (existing parametrized strip tests must still pass — the new regex is a superset).

- [ ] **Step 5: Commit**

```bash
git add src/enricher/lookup.py src/enricher/scorer.py tests/test_lookup.py tests/test_scorer.py
git commit -m "fix: remix designator coverage, MIK-only trailing strip, discogs album, containment guard, genre families"
```

---

### Task 7: Year rule — remix designator detection + Discogs master resolution

**Files:**
- Modify: `src/enricher/lookup.py` (new `has_remix_designator`, new `discogs_master_year`), `src/enricher/enricher.py` (live-lookup branch)
- Test: `tests/test_lookup.py`, `tests/test_enricher.py`

**Interfaces:**
- Produces:
  - `has_remix_designator(title: str) -> bool` — True for remix-type versions (remix/rework/refix/flip/bootleg/vip). "Extended Mix"/"Original Mix"/"Radio Edit" are the original recording → False. Task 8's `_mix_score` consumes this.
  - `discogs_master_year(release_id: str, token: str | None) -> str` — original-release year via the release's master; `""` on any failure (soft-fail: year falls back to the matched pressing's year; this is refinement, not primary lookup).
- Year semantics after this task (spec §2): remix titles keep the matched release's year (the remix's year); non-remix titles get MB `first-release-date` (Task 4) or Discogs master year (this task) — remasters collapse to the original.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Fall Down (Calibre Remix)", True),
        ("Track (VIP)", True),
        ("Track (Somebody's Flip)", True),
        ("Move Your Body (Extended Mix)", False),
        ("Ladbroke Grove", False),
        ("Song (Radio Edit)", False),
        ("Old Track (2015 Remastered)", False),
    ],
)
def test_has_remix_designator(title: str, expected: bool) -> None:
    assert has_remix_designator(title) is expected


@respx.mock
async def test_discogs_master_year_resolves_via_master() -> None:
    respx.get("https://api.discogs.com/releases/9001").respond(json={"id": 9001, "master_id": 555, "year": 2019})
    respx.get("https://api.discogs.com/masters/555").respond(json={"id": 555, "year": 1994})
    assert await discogs_master_year("9001", token=None) == "1994"


@respx.mock
async def test_discogs_master_year_falls_back_to_release_year() -> None:
    respx.get("https://api.discogs.com/releases/9001").respond(json={"id": 9001, "year": 2019})
    assert await discogs_master_year("9001", token=None) == "2019"


@respx.mock
async def test_original_title_gets_master_year_merged_before_cache(tmp_path: Path) -> None:
    discogs_search = {
        "results": [{"id": 9001, "title": "Artist - Release", "year": 2019, "label": ["Some Label"]}]
    }
    respx.get("https://api.discogs.com/database/search").respond(json=discogs_search)
    respx.get("https://api.discogs.com/releases/9001").respond(json={"id": 9001, "master_id": 555, "year": 2019})
    respx.get("https://api.discogs.com/masters/555").respond(json={"id": 555, "year": 1994})
    track = _track(name="Release", artist="Artist", label="", year="")
    cache = EnrichmentCache(tmp_path / "c.json")
    decision = await process_track(track, cache=cache, sources="discogs", use_llm=False, colour_confidence=True)
    assert decision.status == "enriched"
    assert decision.fields_changed.get("year") == ("", "1994")
    cached = cache.get("Artist", "Release")
    assert cached is not None and cached[0].year == "1994"  # refined year persisted with candidates
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_lookup.py tests/test_enricher.py -k "remix_designator or master_year" -v`
Expected: FAIL — functions undefined.

- [ ] **Step 3: Implement**

`src/enricher/lookup.py`:

```python
# Remix-TYPE versions get the remix's year; everything else (Original/Extended/Radio/Remaster)
# is the original recording and gets the earliest release year. Spec §2 year rule.
_REMIX_MARKER_RE = re.compile(
    r"[\(\[][^\)\]]*(?:remix|rework|refix|flip|bootleg|vip)[^\)\]]*[\)\]]", re.IGNORECASE
)


def has_remix_designator(title: str) -> bool:
    return bool(_REMIX_MARKER_RE.search(title))


async def discogs_master_year(release_id: str, token: str | None) -> str:
    """Original-release year via the release's master. Soft-fails to '' — refinement only."""
    delay = _DISCOGS_DELAY_AUTHED if token else _DISCOGS_DELAY_UNAUTHED
    async with _get_discogs_semaphore():
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                await asyncio.sleep(delay)
                rel = await client.get(f"{_DISCOGS_BASE}/releases/{release_id}", headers=_discogs_headers(token))
                rel.raise_for_status()
                rel_data: dict[str, object] = rel.json()
                master_id = rel_data.get("master_id")
                if not master_id:
                    return str(rel_data.get("year") or "")
                await asyncio.sleep(delay)
                mst = await client.get(f"{_DISCOGS_BASE}/masters/{master_id}", headers=_discogs_headers(token))
                mst.raise_for_status()
                mst_data: dict[str, object] = mst.json()
                return str(mst_data.get("year") or "")
        except httpx.HTTPError:
            return ""
```

`src/enricher/enricher.py` — in the live-lookup branch, after the MB-detail block (Task 5) and before `cache.put`:

```python
            # Original-mix titles: resolve Discogs winner to its master for the original year
            probe = score_all(track, candidates)
            if (
                probe
                and probe[0].source == "discogs"
                and probe[0].source_id
                and not track.year
                and not has_remix_designator(track.name)
            ):
                master_year = await discogs_master_year(probe[0].source_id, discogs_token)
                if master_year:
                    for i, c in enumerate(candidates):
                        if c.source == "discogs" and c.source_id == probe[0].source_id:
                            candidates[i] = c.model_copy(update={"year": master_year})
                            break
```

(`has_remix_designator`, `discogs_master_year` join the `enricher.lookup` imports. The duplicate `probe = score_all(...)` lines from Tasks 5 and 7 collapse into one probe computed once after the Discogs conditional; recompute only after a merge mutates candidates.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/enricher/lookup.py src/enricher/enricher.py tests/test_lookup.py tests/test_enricher.py
git commit -m "feat: mix-level year rule — remix keeps its year, originals resolve to master/first release"
```

---

### Task 8: Beatport source + source ordering + mix-aware scoring

**Files:**
- Create: `src/enricher/beatport.py`
- Modify: `src/enricher/models.py` (source literal), `src/enricher/scorer.py` (`_mix_score`, `score_candidate`), `src/enricher/enricher.py` (source ordering), `src/enricher/__main__.py` (`--sources` choices/default), `.env.example`
- Test: `tests/test_beatport.py` (new), `tests/test_scorer.py`, `tests/test_enricher.py`

**Interfaces:**
- Consumes: `SourceLookupError`, `_clean_title`, `_strip_mix_designators`, `_primary_artist` from `enricher.lookup`; `has_remix_designator` (Task 7).
- Produces:
  - `CandidateMatch.source` literal extended to `"musicbrainz" | "discogs" | "beatport"`.
  - `lookup_beatport(track: TrackRecord) -> list[CandidateMatch]` in `enricher.beatport` — candidates carry `mix` (Beatport `mix_name`), real `duration_seconds`, `label`, `year` (publish_date), `remixer`, `album` (release name).
  - Auth env contract: `BEATPORT_API_TOKEN` (static bearer) or `BEATPORT_CLIENT_ID`+`BEATPORT_CLIENT_SECRET` (client-credentials against `https://api.beatport.com/v4/auth/o/token/`). Missing credentials raise `SourceLookupError("beatport", ...)` — the source is skipped with a stderr warning at startup, not a crash (see enricher wiring below).
  - `scorer._mix_score(track_title: str, candidate_mix: str) -> float` folded into `score_candidate`.
  - `--sources` choices: `beatport|musicbrainz|discogs|both|all`; default `all` (Beatport → Discogs → MusicBrainz); `both` keeps its legacy meaning (musicbrainz+discogs).

**Implementer note:** the full official OpenAPI spec is on disk at `~/Development/Beatport API Documentation/` (`catalog.md`, `schemas.md`, `beatport-openapi.json`). Verify the exact query-param names (`name`, `artist_name`, `per_page`) and response field names (`mix_name`, `publish_date`, `length_ms`, `release.label.name`, `remixers`) against `beatport-openapi.json` before finalising `_extract_bp_candidates`, and adjust the respx fixtures to match the spec if they differ. Credentials live in the TuneFinder deployment's env — copy values, not the scraped-client_id code path.

- [ ] **Step 1: Write the failing tests**

`tests/test_beatport.py`:

```python
import pytest
import respx

from enricher.beatport import lookup_beatport
from enricher.lookup import SourceLookupError

_BP_TRACKS_RESPONSE = {
    "results": [
        {
            "id": 777,
            "name": "Fall Down",
            "mix_name": "Calibre Remix",
            "artists": [{"name": "Roni Size"}],
            "remixers": [{"name": "Calibre"}],
            "release": {"name": "Fall Down EP", "label": {"name": "V Recordings"}},
            "publish_date": "2019-03-01",
            "length_ms": 372000,
            "genre": {"name": "Drum & Bass"},
            "isrc": "GBABC1900123",
        }
    ]
}


@respx.mock
async def test_beatport_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_API_TOKEN", "static-token")
    route = respx.get("https://api.beatport.com/v4/catalog/tracks/").respond(json=_BP_TRACKS_RESPONSE)
    track = _track(name="Fall Down (Calibre Remix)", artist="Roni Size")
    cands = await lookup_beatport(track)
    assert route.calls[0].request.headers["Authorization"] == "Bearer static-token"
    c = cands[0]
    assert c.source == "beatport"
    assert c.label == "V Recordings"
    assert c.year == "2019"
    assert c.remixer == "Calibre"
    assert c.mix == "Calibre Remix"
    assert c.duration_seconds == 372


@respx.mock
async def test_beatport_client_credentials_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEATPORT_API_TOKEN", raising=False)
    monkeypatch.setenv("BEATPORT_CLIENT_ID", "cid")
    monkeypatch.setenv("BEATPORT_CLIENT_SECRET", "sec")
    respx.post("https://api.beatport.com/v4/auth/o/token/").respond(json={"access_token": "fresh"})
    respx.get("https://api.beatport.com/v4/catalog/tracks/").respond(json={"results": []})
    await lookup_beatport(_track(name="X", artist="Y"))  # must not raise


async def test_beatport_missing_credentials_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("BEATPORT_API_TOKEN", "BEATPORT_CLIENT_ID", "BEATPORT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SourceLookupError):
        await lookup_beatport(_track(name="X", artist="Y"))
```

`tests/test_scorer.py`:

```python
def test_mix_score_penalises_original_vs_remix_mismatch() -> None:
    # Track is a remix, candidate is the original mix → penalty
    assert _mix_score("Fall Down (Calibre Remix)", "Original Mix") == -0.10
    # Track is original, candidate is a remix → penalty
    assert _mix_score("Fall Down", "Calibre Remix") == -0.10
    # Matching remix name → reward
    assert _mix_score("Fall Down (Calibre Remix)", "Calibre Remix") == 0.05
    # No mix info → neutral
    assert _mix_score("Fall Down", "") == 0.0
```

`tests/test_enricher.py` — source ordering:

```python
@respx.mock
async def test_source_order_beatport_first_skips_others_when_confident(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BEATPORT_API_TOKEN", "t")
    bp = respx.get("https://api.beatport.com/v4/catalog/tracks/").respond(json=_BP_TRACKS_RESPONSE)
    discogs = respx.get("https://api.discogs.com/database/search").respond(json={"results": []})
    mb = respx.get("https://musicbrainz.org/ws/2/recording/").respond(json={"recordings": []})
    track = _track(name="Fall Down (Calibre Remix)", artist="Roni Size", label="", year="", duration_seconds=372)
    decision = await process_track(
        track, cache=EnrichmentCache(tmp_path / "c.json"), sources="all", use_llm=False, colour_confidence=True
    )
    assert decision.status == "enriched"
    assert bp.called and not discogs.called and not mb.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_beatport.py tests/test_scorer.py tests/test_enricher.py -v`
Expected: FAIL — module/function undefined, `source="beatport"` rejected by the models literal.

- [ ] **Step 3: Implement**

`src/enricher/models.py`: `source: Literal["musicbrainz", "discogs", "beatport"]`.

`src/enricher/beatport.py` (new file):

```python
from __future__ import annotations

import asyncio
import os

import httpx

from enricher.lookup import SourceLookupError, _clean_title, _primary_artist, _strip_mix_designators
from enricher.models import CandidateMatch, TrackRecord

_BP_BASE = "https://api.beatport.com/v4"
_BP_TOKEN_URL = "https://api.beatport.com/v4/auth/o/token/"
_BP_DELAY = 0.5  # conservative; no published public rate limit
_MAX_CANDIDATES = 5

_BP_SEMAPHORE: asyncio.Semaphore | None = None
_token_cache: dict[str, str] = {}


def _get_bp_semaphore() -> asyncio.Semaphore:
    global _BP_SEMAPHORE
    if _BP_SEMAPHORE is None:
        _BP_SEMAPHORE = asyncio.Semaphore(1)
    return _BP_SEMAPHORE


async def _get_token() -> str:
    static = os.environ.get("BEATPORT_API_TOKEN", "")
    if static:
        return static
    cached = _token_cache.get("access_token")
    if cached:
        return cached
    cid = os.environ.get("BEATPORT_CLIENT_ID", "")
    secret = os.environ.get("BEATPORT_CLIENT_SECRET", "")
    if not (cid and secret):
        raise SourceLookupError("beatport", "no credentials (set BEATPORT_API_TOKEN or BEATPORT_CLIENT_ID/SECRET)")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(_BP_TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(cid, secret))
            resp.raise_for_status()
            token = str(resp.json()["access_token"])
    except httpx.HTTPError as exc:
        raise SourceLookupError("beatport", f"token request failed: {exc}") from exc
    _token_cache["access_token"] = token
    return token


def _extract_bp_candidates(data: dict[str, object]) -> list[CandidateMatch]:
    results = data.get("results", [])
    out: list[CandidateMatch] = []
    if not isinstance(results, list):
        return out
    for t in results[:_MAX_CANDIDATES]:
        if not isinstance(t, dict):
            continue
        artists = t.get("artists", [])
        artist = (
            ", ".join(str(a.get("name", "")) for a in artists if isinstance(a, dict))
            if isinstance(artists, list)
            else ""
        )
        release = t.get("release") if isinstance(t.get("release"), dict) else {}
        label_obj = release.get("label") if isinstance(release, dict) and isinstance(release.get("label"), dict) else {}
        remixers = t.get("remixers", [])
        remixer = (
            str(remixers[0].get("name", ""))
            if isinstance(remixers, list) and remixers and isinstance(remixers[0], dict)
            else ""
        )
        length_ms = t.get("length_ms")
        publish = str(t.get("publish_date", "") or "")
        mix_name = str(t.get("mix_name", "") or "")
        name = str(t.get("name", "") or "")
        title = f"{name} ({mix_name})" if mix_name and mix_name.lower() not in name.lower() else name
        out.append(
            CandidateMatch(
                source="beatport",
                source_id=str(t.get("id", "")),
                artist=artist,
                title=title,
                label=str(label_obj.get("name", "")) if isinstance(label_obj, dict) else "",
                year=publish[:4],
                remixer=remixer,
                album=str(release.get("name", "")) if isinstance(release, dict) else "",
                mix=mix_name,
                duration_seconds=int(length_ms) // 1000 if isinstance(length_ms, int) else None,
            )
        )
    return out


async def lookup_beatport(track: TrackRecord) -> list[CandidateMatch]:
    token = await _get_token()
    clean = _clean_title(track.name)
    params = {
        "name": _strip_mix_designators(clean),
        "artist_name": _primary_artist(track.artist),
        "per_page": str(_MAX_CANDIDATES),
    }
    async with _get_bp_semaphore():
        await asyncio.sleep(_BP_DELAY)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{_BP_BASE}/catalog/tracks/", params=params, headers={"Authorization": f"Bearer {token}"}
                )
                if resp.status_code == 401:
                    _token_cache.clear()
                    raise SourceLookupError("beatport", "auth rejected (401) — token expired or invalid")
                resp.raise_for_status()
                data: dict[str, object] = resp.json()
                return _extract_bp_candidates(data)
        except httpx.HTTPError as exc:
            raise SourceLookupError("beatport", str(exc)) from exc
```

`src/enricher/scorer.py`:

```python
from enricher.lookup import has_remix_designator


def _mix_score(track_title: str, candidate_mix: str) -> float:
    """Reward mix-name agreement, punish original-vs-remix mismatch.

    Only Beatport candidates carry mix names; others return 0.0 (neutral).
    """
    if not candidate_mix:
        return 0.0
    track_is_remix = has_remix_designator(track_title)
    cand_is_remix = has_remix_designator(f"({candidate_mix})")
    if track_is_remix != cand_is_remix:
        return -0.10
    if track_is_remix and _normalise(candidate_mix) in _normalise(track_title):
        return 0.05
    return 0.02
```

`score_candidate` gains `+ _mix_score(track.name, candidate.mix)` (clamp stays `min(..., 1.0)`; also clamp the floor: `max(round(score, 4), 0.0)`).

`src/enricher/enricher.py` — replace the live-lookup source block (from Task 3) with the spec ordering:

```python
        try:
            def _confident(cands: list[CandidateMatch]) -> bool:
                s = score_all(track, cands)
                return bool(s) and s[0].confidence >= confidence_threshold and bool(s[0].label)

            if sources in ("beatport", "all"):
                try:
                    candidates.extend(await lookup_beatport(track))
                except SourceLookupError as exc:
                    if "no credentials" not in str(exc):
                        raise
                    print(f"WARNING: beatport skipped — {exc}", file=sys.stderr)

            if sources in ("discogs", "both", "all") and not _confident(candidates):
                candidates.extend(await lookup_discogs(track, token=discogs_token))

            if sources in ("musicbrainz", "both", "all") and not _confident(candidates):
                candidates.extend(await lookup_musicbrainz(track))
        except SourceLookupError as exc:
            print(f"ERROR lookup failed for {track.artist} — {track.name}: {exc}", file=sys.stderr)
            return EnrichmentDecision(
                track_id=track.track_id,
                artist=track.artist,
                title=track.name,
                status="skipped_api_error",
            )
```

(The MB-detail and Discogs-master refinement blocks from Tasks 5/7 stay after this, inside the same live branch, before `cache.put`.)

`src/enricher/__main__.py`: `--sources` → `choices=["beatport", "musicbrainz", "discogs", "both", "all"], default="all"`, help: `"Which metadata sources to query (default: all = beatport → discogs → musicbrainz; 'both' = musicbrainz+discogs, legacy)"`.

`.env.example`: add `BEATPORT_API_TOKEN=`, `BEATPORT_CLIENT_ID=`, `BEATPORT_CLIENT_SECRET=`, and the missing `OPENROUTER_API_KEY=` (doc drift fix).

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green. Existing enricher tests that call `process_track(..., sources="both")` keep passing unchanged (legacy path).

- [ ] **Step 5: Commit**

```bash
git add src/enricher/beatport.py src/enricher/models.py src/enricher/scorer.py src/enricher/enricher.py src/enricher/__main__.py .env.example tests/
git commit -m "feat: Beatport primary source with mix-aware scoring and beatport-first ordering"
```

---

### Task 9: LLM cascade — parse-failure fallthrough, valid model id, duration signal

**Files:**
- Modify: `src/enricher/disambiguator.py`, `src/enricher/models.py` (:51)
- Test: `tests/test_disambiguator.py`

**Interfaces:**
- Produces:
  - `DisambigProvider` defined once in `enricher.models` (`Literal["mistral", "groq", "gemini", "openrouter"]`); `disambiguator.py` imports it (kills the drift that caused commit 7d1cadc).
  - `_parse_index(raw: str, num_candidates: int) -> int | None` — `None` = unparseable/out-of-range (cascade continues to next provider); `-1` = model said uncertain (cascade stops); `0..n-1` = chosen.
  - OpenRouter position 4 model id: `openrouter/auto` (replaces invalid `openrouter/free`).
  - Prompt includes durations (track + per-candidate).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
import respx

from enricher.disambiguator import _build_prompt, _parse_index, disambiguate


def test_parse_index_distinguishes_unparseable_from_uncertain() -> None:
    assert _parse_index('{"index": -1}', 3) == -1
    assert _parse_index('{"index": 2}', 3) == 2
    assert _parse_index("total garbage", 3) is None
    assert _parse_index('{"index": 9}', 3) is None  # out of range = misbehaviour, not uncertainty


def _chat_response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


@respx.mock
async def test_parse_failure_falls_through_to_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY", "k2")
    respx.post("https://api.mistral.ai/v1/chat/completions").respond(json=_chat_response("not json at all"))
    respx.post("https://api.groq.com/openai/v1/chat/completions").respond(json=_chat_response('{"index": 0}'))
    idx, provider = await disambiguate(_track(), [_candidate()])
    assert (idx, provider) == (0, "groq")


@respx.mock
async def test_uncertain_answer_stops_cascade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY", "k2")
    respx.post("https://api.mistral.ai/v1/chat/completions").respond(json=_chat_response('{"index": -1}'))
    groq = respx.post("https://api.groq.com/openai/v1/chat/completions")
    idx, provider = await disambiguate(_track(), [_candidate()])
    assert (idx, provider) == (-1, "mistral")
    assert not groq.called


def test_prompt_includes_durations() -> None:
    track = _track(duration_seconds=372)
    cand = _candidate(duration_seconds=371)
    prompt = _build_prompt(track, [cand])
    assert "372" in prompt and "371" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_disambiguator.py -v`
Expected: FAIL — `_parse_index` returns `-1` for garbage (no `None`), garbage from mistral terminates the cascade, prompt lacks durations.

- [ ] **Step 3: Implement**

`src/enricher/models.py` (:51): define `DisambigProvider = Literal["mistral", "groq", "gemini", "openrouter"]` above `EnrichmentDecision` and use it: `disambiguation_used: DisambigProvider | None = None`.

`src/enricher/disambiguator.py`:

```python
from enricher.models import CandidateMatch, DisambigProvider, TrackRecord
```

(delete the local `DisambigProvider` definition at :12).

```python
def _parse_index(raw: str, num_candidates: int) -> int | None:
    """None = unparseable or out-of-range (try the next provider). -1 = model said uncertain (stop)."""
    raw = _strip_thinking(raw).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    try:
        data = json.loads(raw.strip())
        idx = int(data["index"])
    except Exception:
        return None
    if idx == -1 or 0 <= idx < num_candidates:
        return idx
    return None
```

`_build_prompt` — track line and candidate lines gain durations:

```python
        f"Genre: {track.genre} | BPM: {track.bpm} | Key: {track.tonality} | Duration: {track.duration_seconds}s",
```

```python
        lines.append(
            f"  [{i}] {c.artist} — {c.title} | Label: {c.label} | Year: {c.year} | "
            f"Remixer: {c.remixer} | Duration: {c.duration_seconds or '?'}s | Source: {c.source}"
        )
```

Cascade:

```python
async def disambiguate(
    track: TrackRecord,
    candidates: list[CandidateMatch],
) -> tuple[int, DisambigProvider | None]:
    """Return (chosen_index, provider_name) or (-1, None) if unresolved."""
    if not candidates:
        return -1, None

    prompt = _build_prompt(track, candidates)
    providers: list[tuple[DisambigProvider, str | None]] = [
        ("mistral", None),
        ("groq", None),
        ("gemini", None),
        ("openrouter", "openrouter/auto"),
        ("openrouter", "mistralai/mistral-small"),
    ]
    for name, openrouter_model in providers:
        if name == "mistral":
            raw = await _try_mistral(prompt)
        elif name == "groq":
            raw = await _try_groq(prompt)
        elif name == "gemini":
            raw = await _try_gemini(prompt)
        else:
            assert openrouter_model is not None  # noqa: S101 — table above guarantees it
            raw = await _try_openrouter(prompt, openrouter_model)
        if raw is None:
            continue
        idx = _parse_index(raw, len(candidates))
        if idx is None:
            continue  # provider answered garbage — fall through, don't terminate
        return idx, name
    return -1, None
```

Also change the OpenRouter `HTTP-Referer` header (:145) to `"https://github.com/christophechang/rekordbox-meta-data-enrichment"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/enricher/disambiguator.py src/enricher/models.py tests/test_disambiguator.py
git commit -m "fix: LLM cascade falls through on parse failure, openrouter/auto, duration in prompt"
```

---

### Task 10: Per-track containment, startup warnings, reader message

**Files:**
- Modify: `src/enricher/__main__.py` (run loop :120-130, startup), `src/enricher/reader.py` (:63)
- Test: `tests/test_main.py` (new), `tests/test_reader.py`

**Interfaces:**
- Produces: an unexpected exception anywhere in `process_track` (scoring, disambiguation, refinement) yields a `skipped_api_error` decision for that track and the run continues. Startup warns when LLM is enabled with zero provider keys.

- [ ] **Step 1: Write the failing tests**

`tests/test_main.py`:

```python
import pytest

from enricher.__main__ import run


async def test_run_survives_per_track_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    xml = tmp_path / "in.xml"
    xml.write_text(_SOURCE_XML, encoding="utf-8")  # from tests/factories.py (Task 2)

    async def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr("enricher.__main__.process_track", _boom)
    args = _make_args(input=xml, output=tmp_path / "out.xml", no_cache=True, no_llm=True)
    await run(args)  # must not raise
    captured = capsys.readouterr()
    assert "unexpected failure" in captured.err
    assert "Skipped (API error)    : 2" in captured.out


async def test_llm_enabled_without_keys_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ("MISTRAL_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    xml = tmp_path / "in.xml"
    xml.write_text(_SOURCE_XML, encoding="utf-8")
    args = _make_args(input=xml, output=tmp_path / "out.xml", no_cache=True, no_llm=False, limit=0)
    await run(args)
    assert "no provider keys" in capsys.readouterr().err
```

(`_make_args` is a small helper building an `argparse.Namespace` with all defaults from `_parse_args`; include it in the test file:)

```python
import argparse
from pathlib import Path


def _make_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "input": Path("import/rekordbox.xml"),
        "output": Path("out.xml"),
        "report": None,
        "cache": Path(".enrichment_cache.json"),
        "dry_run": False,
        "confidence_threshold": 0.85,
        "sources": "all",
        "no_llm": True,
        "limit": None,
        "no_cache": True,
        "no_colour_confidence": False,
        "full_export": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)
```

`tests/test_reader.py`:

```python
def test_reader_exclusion_message_is_honest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # one soundcloud track + one blank-artist track excluded
    parse_collection(_fixture_with_soundcloud_and_blank_artist(tmp_path))
    err = capsys.readouterr().err
    assert "excluded 2 tracks (SoundCloud/demo/blank-artist)" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main.py tests/test_reader.py -v`
Expected: FAIL — `run` re-raises the RuntimeError; no warning emitted; reader message says "SoundCloud tracks".

- [ ] **Step 3: Implement**

`src/enricher/__main__.py` — in `run()`, before the loop:

```python
    llm_keys = ("MISTRAL_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY")
    if not args.no_llm and not any(os.environ.get(k) for k in llm_keys):
        print(
            "WARNING: LLM disambiguation enabled but no provider keys set — "
            "the 0.65-0.85 confidence band will be skipped.",
            file=sys.stderr,
        )
```

The loop body (:120-130):

```python
        try:
            decision = await process_track(
                track,
                cache=cache,
                sources=sources,
                confidence_threshold=args.confidence_threshold,
                use_llm=not args.no_llm,
                discogs_token=discogs_token,
                colour_confidence=colour_confidence,
            )
        except Exception as exc:  # containment: one bad track never kills the run
            print(f"ERROR unexpected failure for {track.artist} — {track.name}: {exc}", file=sys.stderr)
            decision = EnrichmentDecision(
                track_id=track.track_id, artist=track.artist, title=track.name, status="skipped_api_error"
            )
        decisions.append(decision)
```

`src/enricher/reader.py` (:63):

```python
    print(f"Parsed {len(tracks)} tracks, excluded {excluded} tracks (SoundCloud/demo/blank-artist).", file=sys.stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/enricher/__main__.py src/enricher/reader.py tests/test_main.py tests/test_reader.py
git commit -m "feat: per-track containment, LLM key startup warning, honest reader exclusion message"
```

---

### Task 11: changes.json artifact + winning source in report

**Files:**
- Modify: `src/enricher/reporter.py`, `src/enricher/__main__.py`
- Test: `tests/test_reporter.py`

**Interfaces:**
- Produces:
  - `build_changes(decisions: list[EnrichmentDecision]) -> list[dict[str, object]]` — one entry per changed field: `{"track_id", "artist", "title", "field", "old", "new", "source", "confidence", "colour"}`. This is the frozen interface the daemon plan (Discord report, future db-write agent) consumes — spec §5.3.
  - `changes.json` written next to `--output` (same stem, `.changes.json` suffix) on non-dry runs.
  - Enriched report lines include the winning source, e.g. `[beatport, green]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_reporter.py`:

```python
from enricher.reporter import build_changes, build_report


def _enriched_decision() -> EnrichmentDecision:
    match = CandidateMatch(
        source="beatport", source_id="777", artist="Roni Size", title="Fall Down (Calibre Remix)",
        label="V Recordings", year="2019", confidence=0.91,
    )
    return EnrichmentDecision(
        track_id="42", artist="Roni Size", title="Fall Down (Calibre Remix)", status="enriched",
        match=match, fields_changed={"label": ("", "V Recordings"), "year": ("", "2019")},
        confidence_colour="0x00FF00",
    )


def test_build_changes_one_entry_per_field() -> None:
    changes = build_changes([_enriched_decision()])
    assert len(changes) == 2
    label_change = next(c for c in changes if c["field"] == "label")
    assert label_change == {
        "track_id": "42", "artist": "Roni Size", "title": "Fall Down (Calibre Remix)",
        "field": "label", "old": "", "new": "V Recordings",
        "source": "beatport", "confidence": 0.91, "colour": "0x00FF00",
    }


def test_build_changes_skips_non_enriched() -> None:
    skipped = EnrichmentDecision(track_id="1", artist="A", title="T", status="skipped_no_match")
    assert build_changes([skipped]) == []


def test_report_names_winning_source() -> None:
    report = build_report([_enriched_decision()])
    assert "[beatport, green]" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reporter.py -v`
Expected: FAIL — `build_changes` undefined; report tag lacks source.

- [ ] **Step 3: Implement**

`src/enricher/reporter.py`:

```python
def build_changes(decisions: list[EnrichmentDecision]) -> list[dict[str, object]]:
    """Machine-readable change list — the contract consumed by the daemon's Discord report
    and any future write-back agent (spec §5.3). One entry per changed field."""
    changes: list[dict[str, object]] = []
    for d in decisions:
        if d.status != "enriched" or d.match is None:
            continue
        for field, (old, new) in d.fields_changed.items():
            changes.append(
                {
                    "track_id": d.track_id,
                    "artist": d.artist,
                    "title": d.title,
                    "field": field,
                    "old": old,
                    "new": new,
                    "source": d.match.source,
                    "confidence": d.match.confidence,
                    "colour": d.confidence_colour,
                }
            )
    return changes
```

In `build_report`, the enriched-line tag block (:56-64) — source goes first:

```python
            parts: list[str] = [d.match.source]
            if d.disambiguation_used is not None:
                parts.append(d.disambiguation_used)
            colour_label = _COLOUR_LABEL.get(d.confidence_colour, "")
            if colour_label:
                parts.append(colour_label)
            tag = f"[{', '.join(parts)}]"
```

`src/enricher/__main__.py` — after `write_enriched_xml` (:155-156):

```python
    changes_path = args.output.with_suffix(".changes.json")
    changes_path.write_text(
        json.dumps(build_changes(decisions), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote change list to {changes_path}.", file=sys.stderr)
```

(add `import json` and `from enricher.reporter import build_changes, build_report` to the imports).

- [ ] **Step 4: Run tests to verify they pass**

Run: `ruff format . && ruff check . && mypy . && pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/enricher/reporter.py src/enricher/__main__.py tests/test_reporter.py
git commit -m "feat: changes.json artifact and winning-source report tags"
```

---

## After the final task

- Run the full gate one last time: `ruff format . && ruff check . && mypy . && pytest`
- Update `README.md` and `AGENTS.md`/`CLAUDE.md`: fill-blank-only policy, cache v2 semantics, Beatport source + env vars, `--sources all` default, year rule, changes.json output. Commit as `docs: update README and agent docs for engine v2 behaviour`.
- **Milestone 2 (operator-run, not in this plan):** supervised backfill against the current export with real credentials — `enricher --input <export.xml> --limit 25` first, review the report, then the full run. The 519-track "No Year" smart playlist count dropping toward 0 after import is the acceptance check.
- Opt-in live smoke tests (spec §7 last bullet) are deferred to the daemon plan — they need real Beatport/Discogs credentials, which live in the Mini/TuneFinder deployment.
- The daemon (spec §5, milestone 4) is a separate follow-up plan: `docs/superpowers/plans/<date>-mini-daemon.md` — written after the backfill validates engine behaviour. It will need the `watchdog` dependency (ask before adding, per user rule).
