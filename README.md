# Rekordbox Metadata Enrichment

[![CI](https://github.com/christophechang/rekordbox-meta-data-enrichment/actions/workflows/ci.yml/badge.svg)](https://github.com/christophechang/rekordbox-meta-data-enrichment/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/v/release/christophechang/rekordbox-meta-data-enrichment)](https://github.com/christophechang/rekordbox-meta-data-enrichment/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> This project explores AI-assisted development workflows. My focus here was system design and delivery rather than idiomatic Python, which is not my primary stack.

> Tag your library once. Never manually fill Label, Year, or Remixer again.

Enriches a Rekordbox XML library export with release metadata (label, year, remixer) sourced from Beatport, Discogs, and MusicBrainz. Fills blank fields only — a value already in Rekordbox is never overwritten. Outputs a delta XML containing only updated tracks, ready to re-import into Rekordbox.

---

## Quickstart

```bash
# 1. Install
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Add API keys
cp .env.example .env   # add Beatport/Discogs credentials — LLM keys optional (see Setup)

# 3. Run
cp /path/to/rekordbox.xml import/rekordbox.xml
python -m enricher
```

Import `export/rekordbox_export_YYYY-MM-DD.xml` back into Rekordbox. Done.

---

## Why

Rekordbox has no bulk metadata lookup. Filling Label, Year, and Remixer by hand across thousands of tracks takes hours and stays wrong as your library grows. This tool does it in one command — pulling from Beatport, Discogs, and MusicBrainz, resolving ambiguous matches with an LLM, and writing only blank fields for the tracks that changed back into Rekordbox. Values you've already filled in are never touched.

---

## [What's new in v0.5.0](https://github.com/christophechang/rekordbox-meta-data-enrichment/releases/tag/v0.5.0)

- **Cache hit reporting.** Cache hits are now tracked per-decision and surfaced in both the live progress display and the final enrichment report — so you can see exactly how much work the cache saved on each run.

## [What's new in v0.4.0](https://github.com/christophechang/rekordbox-meta-data-enrichment/releases/tag/v0.4.0)

- **Artist Presents X stripping.** Title strings like `(Aphex Twin Presents Caustic Window)` are cleaned before lookup, removing a common source of false negatives on artist-presented releases.
- **ft./feat./presents in artist lookup.** The primary artist extraction now correctly handles `ft.`, `feat.`, and `presents` separators, preventing featured artists from polluting the MusicBrainz and Discogs queries.

## [What's new in v0.3.0](https://github.com/christophechang/rekordbox-meta-data-enrichment/releases/tag/v0.3.0)

- **BPM-filtered Discogs styles in Mix field.** Relevant Discogs genre/style tags (e.g. `Techno`, `Deep House`) are written to the `Mix` field when no mix designator is present, giving untagged tracks a genre anchor.
- **Prefer original MB release.** MusicBrainz candidate selection now ranks original releases above compilations, remasters, and re-issues, reducing the frequency of incorrect year and label values.

## [What's new in v0.2.0](https://github.com/christophechang/rekordbox-meta-data-enrichment/releases/tag/v0.2.0)

- **Fixed Discogs Format → Mix pollution.** Discogs `Format` descriptions (e.g. `12"`, `EP`) were leaking into the `Mix` field. These are now filtered out at extraction time.
- **Fixed `--full-export` COLLECTION gap.** Tracks in the Rekordbox `COLLECTION` node that were not part of any playlist were being silently dropped from full exports. All tracks are now included.

---

## What it does

- Reads your Rekordbox XML export (`File > Export Collection in xml format`)
- Looks up each track against Beatport, Discogs, and MusicBrainz — in that order, stopping as soon as one is confident
- Fills only blank fields; a value already present in Rekordbox is never overwritten
- Uses LLM disambiguation (Mistral → Groq → Gemini → OpenRouter cascade) for ambiguous matches
- Writes an enriched XML containing only the tracks that changed, plus a machine-readable `changes.json` alongside it
- Colour-codes tracks in Rekordbox by match confidence for easy review

**Fields enriched:** `Label`, `Year`, `Remixer` (plus `Colour` as a confidence marker)

**Fields never touched:** `Name`, `Artist`, `Genre`, `AverageBpm`, `Tonality`, `Comments`, `TotalTime`, `Album`, `Mix` — and any field that's already non-empty

### Example output

```
$ python -m enricher --limit 20

Parsed 20 tracks, excluded 0 tracks (SoundCloud/demo/blank-artist).
Progress: 20/20 | enriched: 14 (live: 2, cached: 12)
Wrote enriched XML to export/rekordbox_export_2026-07-18.xml (14 tracks updated).
Wrote change list to export/rekordbox_export_2026-07-18.changes.json.

========================================================================
REKORDBOX METADATA ENRICHMENT REPORT
========================================================================

SUMMARY
----------------------------------------
  Total tracks processed : 20
  Enriched               : 14 (live: 2, cached: 12)
  Already complete       : 2
  Skipped (low conf.)    : 1
  Skipped (no match)     : 2
  Skipped (API error)    : 1

LLM DISAMBIGUATION CALLS
----------------------------------------
  groq        : 1
  mistral     : 2

ENRICHED TRACKS
------------------------------------------------------------------------
  Aphex Twin — Windowlicker [beatport, green]
    label     : '(empty)'                      → 'Warp Records'
    year      : '(empty)'                      → '1999'
  Bicep — Glue [discogs, mistral, orange]
    label     : '(empty)'                      → 'Ninja Tune'
    year      : '(empty)'                      → '2017'

  ... 12 more

UNRESOLVED — LOW CONFIDENCE (review manually)
------------------------------------------------------------------------
  DJ Shadow — Midnight In A Perfect World | best: DJ Shadow — Midnight In A Perfect World (musicbrainz, conf=0.71)

UNRESOLVED — NO MATCH FOUND
------------------------------------------------------------------------
  Unknown Artist — Track 04
  Unknown Artist — Track 09

SKIPPED — API ERRORS
------------------------------------------------------------------------
  Four Tet — Skip (timeout)

========================================================================
```

Each enriched line is tagged with the winning source, the LLM provider if disambiguation was used, and the confidence colour — e.g. `[beatport, green]` or `[discogs, mistral, orange]`.

---

## Confidence colour coding

By default, every match is applied and the Rekordbox `Colour` field is set as a confidence signal:

| Colour | Meaning |
|--------|---------|
| Green | High confidence (≥ 0.85) — auto-matched, safe to use |
| Orange | Medium confidence (0.65–0.85) — LLM-assisted, worth a glance |
| Red | Low confidence (< 0.65) or heuristic label — inspect before relying on |
| Blank | No match found anywhere |

Pass `--no-colour-confidence` to skip low-confidence matches entirely and only apply green/orange.

---

## Setup

**Requirements:** Python 3.12+

macOS ships with Python 3.9. You likely need to use your Homebrew Python explicitly. Check with `which python3.13` or `which python3.12` and substitute below:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and populate your API keys:

```bash
cp .env.example .env
```

```
# LLM disambiguation — provider cascade (first available is used)
# All optional — without any key, the 0.65-0.85 confidence band is skipped
# (a startup warning is printed; the run still completes)
MISTRAL_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=

# Discogs — personal access token (discogs.com → Settings → Developers)
# Optional but recommended: raises rate limit from 25 to 60 req/min
DISCOGS_TOKEN=

# Beatport — primary metadata source. Your account username + password drive a
# PKCE authorization-code login. Optional — leave blank to skip Beatport (falls
# back to Discogs → MusicBrainz)
BEATPORT_USERNAME=
BEATPORT_PASSWORD=
```

Every key above is optional:

- **LLM keys** — Mistral is preferred; Groq, Gemini, and OpenRouter (tried twice: `openrouter/auto`, then `mistralai/mistral-small`) are free-tier fallbacks. Used only for the 0.65–0.85 confidence band — with none configured, a startup warning is printed and that band is simply skipped.
- **`DISCOGS_TOKEN`** — works unauthenticated too, just slower.
- **Beatport credentials** (`BEATPORT_USERNAME` + `BEATPORT_PASSWORD`) — your account login drives a PKCE authorization-code flow; tokens are cached in a chmod-600 `.beatport_token.json`. Without both, Beatport is skipped with a warning and lookup falls back to Discogs → MusicBrainz.

---

## Usage

**Place your Rekordbox XML export at `import/rekordbox.xml`**, then run:

```bash
python -m enricher
```

The enriched delta XML is written to `export/rekordbox_export_YYYY-MM-DD.xml`.

### All options

```
python -m enricher [OPTIONS]

  --input PATH              Source Rekordbox XML (default: import/rekordbox.xml)
  --output PATH             Output XML path (default: export/rekordbox_export_YYYY-MM-DD.xml)
  --report PATH             Write enrichment report to file instead of stdout
  --cache PATH              Cache file path (default: .enrichment_cache.json)
  --confidence-threshold N  Minimum score for auto-enrichment, 0–1 (default: 0.85)
  --sources CHOICE          beatport | musicbrainz | discogs | both | all (default: all)
                            all = beatport → discogs → musicbrainz; both = musicbrainz + discogs (legacy)
  --no-llm                  Skip LLM disambiguation, use auto-confidence only
  --no-colour-confidence    Only apply high-confidence matches, skip low-confidence
  --limit N                 Process only first N tracks — useful for test runs
  --no-cache                Disable cache reads and writes
  --dry-run                 Preview what would change, write no files
  --full-export             Guarantee every track appears in exactly one output playlist.
                            Enriched and already-complete tracks → "Updated Tracks";
                            no-match, low-confidence, and API-error tracks → "Unable to Enrich".
                            Use this to produce a definitive source-of-truth export after a full run.
```

### Common workflows

```bash
# Test run — first 10 tracks, no cache, no files written
python -m enricher --limit 10 --no-cache --dry-run

# Full run (default — colour confidence on, all sources: beatport → discogs → musicbrainz, LLM enabled)
# First v2 run against a library you've enriched before? Delete .enrichment_cache.json first —
# v1-migrated entries predate Beatport and would otherwise replay without ever querying it.
python -m enricher

# Strict mode — only apply high-confidence matches
python -m enricher --no-colour-confidence

# Save report to file
python -m enricher --report reports/enrichment_2026-03-20.txt

# MusicBrainz only (faster, no Beatport/Discogs credentials needed)
python -m enricher --sources musicbrainz

# Source-of-truth export — every track in exactly one playlist
# "Updated Tracks" = enriched or already complete
# "Unable to Enrich" = no match, low confidence, or API error
# Use this after a full run to verify complete coverage and for clean re-import
python -m enricher --full-export
```

---

## How it works

### Pipeline (per track)

```
Already complete (has Label AND Year)? ──yes──► Skip (checked first, every run — before the cache)
    │
    no
    │
Cache hit? ──yes──► Reuse cached candidates, skip straight to scoring
    │
    no
    │
Beatport → Discogs → MusicBrainz — each runs only if the one before
it isn't already confident (see Source resolution, below)
    │
Score all candidates
    │
No candidates ──► Heuristic label (bootleg/white label) match, or skip (no match — not cached)
    │
Best score ≥ 0.85 ──► Auto-enrich (green) — writes only fields still blank
    │
Best score 0.65–0.85 ──► LLM cascade ──► Enrich if resolved (orange)
    │
Best score 0.30–0.65 ──► Apply with red (colour-confidence mode only)
    │
Score < 0.30 ──► Skip (blank colour)

A lookup failure at any point ──► skipped_api_error (never cached, retried next run)
```

### Source resolution

`--sources all` (the default) queries in this order, stopping early once a source produces a confident match with a label:

1. **Beatport** — official API v4. Skipped with a warning if no credentials are configured. Issues a single query per track (primary artist + mix-stripped title) — no fallback ladder. Matching is mix-aware: Beatport tracks are mix-level entities, so the API's own `mix_name` is compared against the title's mix designator.
2. **Discogs** — runs only if Beatport didn't already produce a confident, labelled match.
3. **MusicBrainz** — runs only if the score is still below threshold. When MusicBrainz wins and `Label`/`Remixer` are still blank, a follow-up recording-detail lookup fetches them (MusicBrainz search responses don't include that data).

`--sources both` restricts this to the legacy MusicBrainz + Discogs pair (no Beatport). `--sources beatport` / `musicbrainz` / `discogs` restrict to one source only.

### Query strategy

MusicBrainz and Discogs each go through up to three attempts before giving up:

1. Full artist string + cleaned title (catalogue numbers, trailing BPM stripped)
2. Primary artist only (before `/`, `,`, `&`, `x`, `vs`, `feat.`, `presents`) + cleaned title
3. Primary artist + title with mix designators stripped (`(Original Mix)`, `(feat. X)`, etc.)

### Confidence scoring

```
score = artist_similarity (0–0.40)
      + title_similarity  (0–0.40)
      + duration_match    (0–0.15)
      + genre_bonus       (0–0.05)
      + mix_agreement     (−0.10 to 0.05)
```

`mix_agreement` only applies to Beatport candidates (the only source with a mix name): a penalty when the track's remix status and the candidate's mix disagree, a small bonus when they agree.

### Year rule

- **Title carries a remix designator** (`Remix`, `Rework`, `Refix`, `Flip`, `VIP`, `Bootleg`) — the year is that remix's own release year.
- **No remix designator** (`Original Mix`, `Extended Mix`, `Radio Edit`, `Remaster`, or nothing at all) — the year is the *earliest* release year of the recording: MusicBrainz's recording-level `first-release-date`, or for Discogs, a follow-up lookup resolves the release to its master year. A remaster collapses to its original's year, never the remaster's own release date.
- **Beatport year is that track's earliest Beatport publish date** among the candidates a query returns — when two candidates score identically (same name/mix/artist, e.g. an original vs. a Beatport reissue), the earlier `publish_date` wins the tie. This is only as accurate as Beatport's own catalogue: pre-Beatport-era originals (pre ~2004) still resolve correctly via the Discogs-master / MusicBrainz-first-release-date path above whenever Beatport's candidates lack a year or tie with each other, but a re-release that's the *only* version Beatport has indexed may still carry that reissue's year.

### LLM disambiguation

When candidates cluster in the 0.65–0.85 band, an LLM is asked to pick the best match given the track's genre, BPM, Camelot key, and duration. Provider cascade — each step runs only if the previous one has no key configured, errors, or returns an unparseable response (a parse failure falls through to the next provider rather than ending the cascade):

1. **Mistral** (`mistral-small-latest`)
2. **Groq** (`llama-3.3-70b-versatile`, free tier)
3. **Gemini** (`gemini-2.5-flash`, free tier)
4. **OpenRouter** (`openrouter/auto`)
5. **OpenRouter** (`mistralai/mistral-small`)

All LLM keys are optional. With none configured, a startup warning is printed and the 0.65–0.85 band is skipped rather than the run failing. If every configured provider fails or returns uncertain (`-1`), the track is left unenriched.

### Cache

Results are stored in `.enrichment_cache.json` — **raw lookup candidates only, never decisions.** Every run re-scores the cached candidates against the track's current state and the active CLI flags, so a manual correction made in Rekordbox, a changed `--confidence-threshold`, or `--no-colour-confidence` all take effect immediately on a cache hit.

`--sources` is the exception: a cache hit replays its stored candidates as-is and skips all lookups entirely, regardless of which sources are currently selected — the candidates on disk already reflect whatever sources produced them on the original, uncached run. To re-query a track against a different `--sources` value, delete `.enrichment_cache.json` (or the relevant entries) or run with `--no-cache`.

- **Never cached:** empty candidate lists (no-match) and API errors — both retried on every run, so query improvements or a flaky API automatically recover previously-missed tracks.
- **Already-complete tracks bypass the cache entirely** — completeness is checked first, before any cache lookup, so the cache can never re-clobber a track you've since filled in by hand.
- **Writes are atomic** (temp file + rename) and flushed periodically during a run, not just at the end.
- **v1 cache files migrate automatically** on load — candidates are salvaged, any previously-stored decision is discarded, and the file is rewritten in the new shape on the next flush.
- **A corrupt cache file never crashes a run** — it's renamed to a `.corrupt-<timestamp>` backup, a warning is printed, and the run starts with a fresh cache.

### Heuristic labels

When no configured source returns a candidate and the track title or artist contains `bootleg`, `white label`, `unofficial`, `free dl`, or `free download`, the label is set to `Bootleg` or `White Label` automatically (shown in red).

---

## Output

### Export XML

The output contains **only tracks that changed** — not the full library. This keeps re-import fast and avoids overwriting unrelated data.

A `Updated Tracks` playlist is included in the export. After importing into Rekordbox, this playlist contains all updated tracks for easy review.

### Report

The enrichment report (printed to stdout or `--report` file) includes:

- Summary counts (enriched — split into live vs. cached — already complete, low confidence, no match, API errors)
- LLM disambiguation call counts per provider
- Per-track field changes for enriched tracks, each tagged with its winning source, LLM provider (if used), and confidence colour
- Unresolved tracks for manual review, including API-error tracks (never cached, so they're retried next run)

### changes.json

Written next to the output XML as `<output-name>.changes.json` (e.g. `rekordbox_export_2026-07-18.changes.json`) — one entry per changed field, machine-readable:

```json
[
  {
    "track_id": "100",
    "artist": "Aphex Twin",
    "title": "Windowlicker",
    "field": "label",
    "old": "",
    "new": "Warp Records",
    "source": "beatport",
    "confidence": 0.97,
    "colour": "0x00FF00"
  }
]
```

---

## Excluded tracks

Excluded tracks aren't itemized individually, but the total is printed at startup: `Parsed N tracks, excluded M tracks (SoundCloud/demo/blank-artist).`

- **SoundCloud tracks** (`Location` starts with `file://localhostsoundcloud`)
- **Rekordbox demo tracks** (`Artist = "rekordbox"`)
- **Empty/corrupted artist field**

---

## Re-importing into Rekordbox

1. In Rekordbox: `File > Import Collection from xml format`
2. Select `export/rekordbox_export_YYYY-MM-DD.xml`
3. Rekordbox will update the matching tracks in your library
4. Open the `Updated Tracks` playlist to review changes
5. Use the colour filter to sort by confidence: green = keep, orange = check, red = verify

---

## Development

```bash
ruff format .        # format
ruff check .         # lint
mypy .               # type check
pytest               # run tests
pytest --tb=short -q # terse output
```

All tests mock HTTP calls — no real API requests are made during testing.

---

## Project structure

```
src/enricher/
├── __main__.py       CLI entry point and async orchestration
├── models.py         Pydantic models (TrackRecord, CandidateMatch, EnrichmentDecision)
├── reader.py         Rekordbox XML parser
├── lookup.py         MusicBrainz + Discogs async lookups with rate limiting
├── beatport.py       Beatport API v4 client (primary source)
├── scorer.py         Confidence scoring algorithm
├── disambiguator.py  LLM disambiguation (Mistral → Groq → Gemini → OpenRouter cascade)
├── enricher.py       Per-track pipeline orchestration (fill-blank-only policy)
├── cache.py          Persistent JSON cache (candidates, not decisions)
├── writer.py         Enriched delta XML output (write whitelist)
└── reporter.py       Enrichment report + changes.json generator
```
