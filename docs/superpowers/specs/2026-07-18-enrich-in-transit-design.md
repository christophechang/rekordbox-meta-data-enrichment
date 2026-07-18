# Enrich-in-Transit — Design Spec

Date: 2026-07-18
Status: awaiting user review
Repo: RekordboxMetaDataEnrichment (engine + new daemon)

## 1. Context and goal

The enrichment CLI fills Label/Year in Rekordbox XML via MusicBrainz + Discogs + LLM disambiguation. It works (876/2,354 enriched historically) but has correctness flaws that justify the operator's overwrite fear, and the run loop is manual (terminal on the Mac Mini).

Two goals, two workstreams:

1. **Engine correctness** — the tool does a much better job: precision-first matching, fill-blank-only writes, Beatport as primary source, honest error handling.
2. **Seamless loop** — the operator's only manual actions become: export XML in Rekordbox (existing habit; a watcher already copies it to the Mac Mini) and, when notified, `Import Playlist` + confirm in Rekordbox. Everything between runs unattended on the Mini.

Environment facts: Rekordbox 7.2.16 on MacBook Air (audio files + master.db there; Time Machine backup; no Pioneer cloud sync). Mac Mini (192.168.1.122) runs launchd automations (`com.openclaw.*`) with Cloudflare tunnels. Existing watcher copies each Rekordbox XML export from the Air to the Mini. Library: 2,124 tracks; 519 missing Label; 3 missing Year.

## 2. Decisions (locked with operator)

- **Fill-blank-only** write policy. Never replace a non-empty field. `--replace` reserved as future explicit opt-in (not built now).
- **Auto-apply, no review UI.** Colour confidence contract unchanged: green ≥0.85, orange 0.65–0.85, red <0.65 (matches MixLab's reader).
- **Year semantics**: title carries a remix designator → the remix's release year; otherwise earliest release year of that recording (remaster collapses to original). Mix-level recording identity.
- **Beatport = primary source** (official API v4; operator holds proper credentials from recent TuneFinder work). Order: Beatport → Discogs → MusicBrainz → LLM disambiguation for the 0.65–0.85 band only.
- **Write-back channel = Rekordbox XML bridge import** (proven operator workflow: Imported Library → playlist → Import Playlist → "replace metadata" OK). Direct master.db writes via pyrekordbox: rejected for now (unsupported risk; import channel works). May revisit only if the two-click import ever becomes friction.
- Manual XML export in Rekordbox stays the pipeline trigger (deliberate "commit" action).

## 3. Architecture

```
MacBook Air                          Mac Mini                                   Azure
Rekordbox 7.2.16                     daemon (launchd + watchdog)
  └─ File > Export Collection ──existing watcher──▶ WATCH_PATH/rekordbox.xml
                                        │  stability-wait → lock → enrich run
                                        │  (fill-blank-only, candidate cache)
                                        ├─▶ ENRICHED_PATH/rekordbox.enriched.xml
                                        │     └─▶ POST /api/mixlab/uploads (gzip, bearer)
                                        │            └─▶ mixlab-web "use latest upload"
                                        ├─▶ enrichment-import.xml ──reverse copy──▶ Air (fixed path)
                                        ├─▶ changes.json + library_snapshot.json (artifacts)
                                        └─▶ Discord webhook: summary + import file attached
Air, when pinged: rekordbox xml tree → "Enriched YYYY-MM-DD" playlist → Import Playlist → OK
```

Components:
- **Engine** (existing `src/enricher/`, heavily fixed — §4).
- **Daemon** (new `src/enricher/daemon.py` + console entry): watchdog on `WATCH_PATH`, file-stability wait (mtime+size stable ~5s to guard partial copies), lock file to serialise runs, then invokes the engine and publishes outputs (§5).
- **Reverse copy**: extension of the operator's existing watcher tooling (Mini → Air fixed path). Outside this repo; interface = "file appears at `IMPORT_XML_PATH` on the Air".

## 4. Workstream 1 — engine fixes

Ordered; file refs are current code.

1. **Fill-blank-only policy.** `_fields_changed` (enricher.py:86-88) proposes values only for empty fields; writer sets only currently-empty attrs (writer.py:85-88). Completeness check (label AND year) unchanged.
2. **Cache stores candidates, not decisions** (cache.py). Every run replays cached candidates → scorer → policy against current track state. Fixes: stale `track_id` on hits, mode-blind cached decisions, re-clobbering of manual corrections (completeness re-checked per run), frozen `skipped_low_confidence` entries. Preserved asymmetry (load-bearing): empty candidate sets and API errors are never cached. Atomic cache writes (tmp+rename); corrupt cache file → back up, rebuild, warn — never crash.
3. **Beatport source, position 1** (new `lookup_beatport`). Official v4 (`api.beatport.com/v4/catalog/...`), credentials from env (shape verified at implementation; reuse the official key from TuneFinder work — not the scraped-client_id path). Yields label, publish_date, genre/sub-genre, mix_name, remixers, ISRC, BPM, key. Matching is mix-aware: Beatport tracks are mix-level entities; compare `mix_name` against the title's designator.
4. **MusicBrainz repairs** (lookup.py):
   - Drop the no-op `inc=` from the search call (lookup.py:196); search responses never contain `label-info`/`relations`, so current label/remixer extraction is dead in production.
   - Year from recording-level `first-release-date` (present in search responses; correct "original year" source).
   - Label/remixer via follow-up `/recording/{mbid}?inc=releases+labels+artist-rels` only when MB wins and the field is still missing (rate-budget conscious).
   - Honest rate limit: 1 req/s (current comment claims 3 req/s — wrong; lookup.py:19-20).
   - No bare `except Exception: return []` (lookup.py:206, 285-286). HTTP failures surface as `skipped_api_error`; retry with backoff honouring `Retry-After` on 503.
5. **Year rule implementation**: remix designator in title → matched remix release year (Beatport publish_date / matched release). No designator → earliest: MB `first-release-date`; Discogs resolved release → master → main-release year.
6. **Matching fixes**:
   - `(Artist Remix)` pattern added to `_MIX_DESIGNATOR_RE` (lookup.py:49-57, query fallback only) and scorer `_REMIX_RE` (scorer.py:9-14, mix-mismatch penalty).
   - Lucene escaping for MB query strings (lookup.py:191).
   - `_TRAILING_BPM_RE` (lookup.py:43) tightened so legitimate trailing numbers ("Xpander 2") survive.
   - Discogs Album = title part only, never "Artist - Title" (lookup.py:258).
   - Artist containment score gets a length guard (scorer.py:129).
   - Genre-family bonus gap: perfect Discogs match on genres missing from `_GENRE_FAMILIES` scores 0.80 and needlessly burns an LLM call — extend families or rebalance.
7. **LLM cascade** (disambiguator.py): parse failure falls through to the next provider (currently terminates cascade, :162-164); replace invalid `openrouter/free` model id; add candidate duration to the prompt (strongest signal, currently omitted). Free-tier cascade (Mistral → Groq → Gemini → OpenRouter) stays.
8. **Reporting**: per-track winning source in the report; `changes.json` as machine-readable output.
9. **Per-track exception containment**: wrap the full per-track pipeline (currently only lookups are wrapped — enricher.py:122-135); one bad track never aborts a run.

## 5. Workstream 2 — daemon and output contracts

Run = triggered by a new/changed XML at `WATCH_PATH` (or manual CLI invocation, which stays supported).

Outputs per run:

1. **`rekordbox.enriched.xml`** — full collection, all blanks filled where matched, Colour set. Atomic write. **Fidelity contract: diff vs source XML = blank→filled attribute changes + Colour changes, nothing else** (enforced by test — §7). Consumers: auto-POST gzipped to `/api/mixlab/uploads` (existing endpoint, `MixLab:ApiSecret` bearer) so mixlab-web "use latest upload" serves enriched data; local path available for Mini-side CLI runs via `MIXLAB_COLLECTION_PATH`.
2. **`enrichment-import.xml`** — changed tracks only + review playlists ("Enriched YYYY-MM-DD", "Review — Unable to Enrich", per current writer behaviour writer.py:20-49). Stable filename. Reverse-copied to a fixed path on the Air so Rekordbox's Imported Library setting is configured once. Track rows are verbatim copies of the source export (children — TEMPO/POSITION_MARK — and unknown attrs untouched) with only the filled fields changed, making the Rekordbox "replace metadata" confirmation safe by construction.
3. **`changes.json`** — `[{track_id, location, field, old: "", new, source, confidence}]`. Report artifact; also the frozen interface if a db-write agent is ever built.
4. **`library_snapshot.json`** — artist/title/label/year/genre + normalised identity key per track. Produced for the future TuneFinder "already-own"/label-affinity seam; TuneFinder-side consumption is out of scope here.
5. **Discord webhook** — summary (filled / no-match / low-confidence counts, change list, winning sources) + `enrichment-import.xml` attached (delta is tens of KB). Failure alerts on daemon errors. No changes → no import-file update, message says so.

Config via `.env` (repo convention): `WATCH_PATH`, `ENRICHED_PATH`, `IMPORT_XML_PATH`, `MIXLAB_API_URL`, `MIXLAB_API_SECRET`, `DISCORD_WEBHOOK_URL`, `BEATPORT_*`, `DISCOGS_TOKEN`, LLM keys. Deployment: `com.openclaw.rekordbox-enricher.plist`, `OpenClaw/Automations/` tree, logs to file (Mini conventions).

## 6. Operator routine (after)

1. Rekordbox → File → Export Collection (unchanged habit; watcher ships it).
2. Discord ping arrives: "N labels filled — import file ready".
3. Rekordbox → rekordbox xml tree → "Enriched YYYY-MM-DD" → Import Playlist → confirm replace. Done.

Known caveat (accepted): edits made in Rekordbox between export and import are reverted for the changed tracks only (their XML rows carry export-time state, including cues/rating/play count). Exposure is small (delta-sized) and shrinks by importing soon after the ping.

Habit note: avoid manually uploading raw XML in mixlab-web after a run — the daemon's enriched upload is the newest; a later manual raw upload would supersede it until the next enrichment run.

## 7. Testing

Existing pytest/respx style; no live calls by default.

- **Fidelity contract test**: source XML with TEMPO/POSITION_MARK children, unknown attrs, odd encodings → enriched output diff contains only blank→filled attrs + Colour. Byte-level survival of untouched content.
- Fill-blank-only policy tests (non-empty fields never proposed/written).
- Cache-replay semantics: candidates cached, decisions recomputed; empty-candidates and api-error never cached (pin the load-bearing asymmetry); corrupt cache file recovery.
- Corrected MB mocks: search responses without `label-info`/`relations`, with `first-release-date`; follow-up recording lookup mocked separately.
- Fallback-sequence tests: attempts 1→2→3 fire with expected params on empty results.
- Beatport fetcher tests (auth header, search, mix-aware matching, ISRC path).
- LLM cascade: parse-failure falls through; all five positions reachable.
- Daemon: stability-wait, lock serialisation, no-crash on malformed XML.
- Opt-in live smoke tests (marked, excluded by default) for Beatport/MB/Discogs.

## 8. Phasing

- **Milestone 1 — engine correct** (§4 items 1, 2, 4, 6, 9 minimum + tests). CLI still usable manually.
- **Milestone 2 — backfill**: one supervised run over the current export; 519 labels filled; operator imports; verify in Rekordbox.
- **Milestone 3 — Beatport + year rule** (§4 items 3, 5, 7, 8) — can swap ahead of milestone 2 if credentials are ready; backfill benefits from Beatport.
- **Milestone 4 — daemon + outputs** (§5) + Mini deployment + reverse copy.
- **Later / separate specs**: verify-pass on existing years (report-only, flags repress-year suspects from earlier runs); TuneFinder snapshot consumption; purchase ledger (TuneFinder "bought" feedback → enricher source position 0); pyrekordbox db-write agent (only if import friction ever matters); genre normalisation (needs the single-taxonomy decision across MixLab/mixlab-web/TuneFinder/enricher).

## 9. Invariants (enforced, documented in code)

- `Name`/`Artist` attributes are never modified (protects MixLab's played/unplayed catalog join, which matches on normalised artist+title).
- Source XML is never modified; all outputs are separate files, written atomically.
- Non-empty fields are never replaced.
- A failed run leaves the previous enriched XML and import file in place.

## 10. Out of scope

Genre/mood enrichment, review UI, .NET API changes beyond using the existing uploads endpoint, TuneFinder/MixLab repo changes (except the one-line `MIXLAB_COLLECTION_PATH` note for Mini-side CLI runs), Album enrichment (pollution bug fixed; field not actively enriched), any Rekordbox database writes.

## 11. Open implementation items

- Exact `WATCH_PATH` (watcher's drop location on the Mini) and the Air-side fixed import path.
- Beatport credential shape (key/secret vs token) — read from the TuneFinder deployment when building.
- Discord destination (existing #music-research vs new channel) — operator picks at deploy.
