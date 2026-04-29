# Rekordbox Metadata Enrichment

[![CI](https://github.com/christophechang/rekordbox-meta-data-enrichment/actions/workflows/ci.yml/badge.svg)](https://github.com/christophechang/rekordbox-meta-data-enrichment/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/v/release/christophechang/rekordbox-meta-data-enrichment)](https://github.com/christophechang/rekordbox-meta-data-enrichment/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> Tag your library once. Never manually fill Label, Year, or Remixer again.

Enriches a Rekordbox XML library export with release metadata (label, year, remixer, album, mix) sourced from MusicBrainz and Discogs. Outputs a delta XML containing only updated tracks, ready to re-import into Rekordbox.

---

## Quickstart

```bash
# 1. Install
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Add API keys
cp .env.example .env   # then fill in at least one LLM key + optional DISCOGS_TOKEN

# 3. Run
cp /path/to/rekordbox.xml import/rekordbox.xml
python -m enricher
```

Import `export/rekordbox_export_YYYY-MM-DD.xml` back into Rekordbox. Done.

---

## Why

Rekordbox has no bulk metadata lookup. Filling Label, Year, and Remixer by hand across thousands of tracks takes hours and stays wrong as your library grows. This tool does it in one command — pulling from MusicBrainz and Discogs, resolving ambiguous matches with an LLM, and writing only the tracks that changed back into Rekordbox.

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
- Looks up each track against MusicBrainz (primary) and Discogs (secondary)
- Uses LLM disambiguation (Mistral → Groq → Gemini cascade) for ambiguous matches
- Writes an enriched XML containing only the tracks that changed
- Colour-codes tracks in Rekordbox by match confidence for easy review

**Fields enriched:** `Label`, `Year`, `Remixer`, `Album`, `Mix`

**Fields never touched:** `Name`, `Artist`, `Genre`, `AverageBpm`, `Tonality`, `Comments`, `TotalTime`

### Example output

```
$ python -m enricher --limit 20

Processing 20 tracks...
████████████████████ 20/20  [cache: 12 hits]

── Enrichment Report ────────────────────────────────────────
Enriched            14    (12 from cache, 2 new)
Already complete     2
Low confidence       1
No match             2
Errors               1

LLM disambiguation   3 calls  (Mistral: 2, Groq: 1)

── Changes ──────────────────────────────────────────────────
Aphex Twin – Windowlicker
  Label:   —  →  Warp Records
  Year:    —  →  1999
  Remixer: —  →  Aphex Twin

Bicep – Glue
  Label:   —  →  Ninja Tune
  Year:    —  →  2017

... 12 more

── Unable to Enrich ─────────────────────────────────────────
DJ Shadow – Midnight In A Perfect World  (low confidence: 0.71)
Unknown Artist – Track 04
```

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
MISTRAL_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=

# Discogs — personal access token (discogs.com → Settings → Developers)
# Optional but recommended: raises rate limit from 25 to 60 req/min
DISCOGS_TOKEN=
```

At least one LLM key is required for disambiguation. Mistral is preferred. Groq and Gemini have free tiers.

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
  --sources CHOICE          musicbrainz | discogs | both (default: both)
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

# Full run (default — colour confidence on, both sources, LLM enabled)
python -m enricher

# Strict mode — only apply high-confidence matches
python -m enricher --no-colour-confidence

# Save report to file
python -m enricher --report reports/enrichment_2026-03-20.txt

# MusicBrainz only (faster, no Discogs token needed)
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
Cache hit? ──yes──► Return cached decision
    │
    no
    │
Already complete (has label + year)? ──yes──► Skip
    │
    no
    │
MusicBrainz lookup ──► Score candidates
    │
Best score < threshold? ──yes──► Discogs lookup ──► Merge + re-score
    │
Best score ≥ 0.85 ──► Auto-enrich (green)
    │
Best score 0.65–0.85 ──► LLM disambiguation ──► Enrich if resolved (orange)
    │
Best score 0.30–0.65 ──► Apply with red (colour-confidence mode only)
    │
No candidates / score < 0.30 ──► Skip (blank colour)
```

### Query strategy

Each lookup goes through up to three attempts before giving up:

1. Full artist string + cleaned title (catalogue numbers, trailing BPM stripped)
2. Primary artist only (before `/`, `,`, `&`, `x`, `vs`) + cleaned title
3. Primary artist + title with mix designators stripped (`(Original Mix)`, `(feat. X)`, etc.)

### Confidence scoring

```
score = artist_similarity (0–0.40)
      + title_similarity  (0–0.40)
      + duration_match    (0–0.15)
      + genre_bonus       (0–0.05)
```

### LLM disambiguation

When candidates cluster in the 0.65–0.85 band, an LLM is asked to pick the best match given the track's genre, BPM and Camelot key. Provider cascade:

1. **Mistral** (`mistral-small-latest`)
2. **Groq** (llama-3.3-70b, free tier)
3. **Gemini 2.5 Flash** (free tier)

If all providers fail or return uncertain, the track is left unenriched.

### Cache

Results are stored in `.enrichment_cache.json`. Successful matches and already-complete tracks are cached permanently. API errors and no-match results are **never** cached — they are retried on every run so that query improvements automatically recover previously missed tracks.

### Heuristic labels

When both APIs return nothing and the track title or artist contains `bootleg`, `white label`, `unofficial`, `free dl`, or `free download`, the label is set to `Bootleg` or `White Label` automatically (shown in red).

---

## Output

### Export XML

The output contains **only tracks that changed** — not the full library. This keeps re-import fast and avoids overwriting unrelated data.

A `Updated Tracks` playlist is included in the export. After importing into Rekordbox, this playlist contains all updated tracks for easy review.

### Report

The enrichment report (printed to stdout or `--report` file) includes:

- Summary counts (enriched / already complete / low confidence / no match / errors)
- Cache hit count
- LLM disambiguation call counts per provider
- Per-track field changes for enriched tracks
- Unresolved tracks for manual review

---

## Excluded tracks

- **SoundCloud tracks** (`Location` starts with `file://localhostsoundcloud`) — skipped silently
- **Rekordbox demo tracks** (`Artist = "rekordbox"`) — skipped silently
- **Empty/corrupted artist field** — skipped silently

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
├── scorer.py         Confidence scoring algorithm
├── disambiguator.py  LLM disambiguation (Mistral → Groq → Gemini cascade)
├── enricher.py       Per-track pipeline orchestration
├── cache.py          Persistent JSON cache
├── writer.py         Enriched delta XML output
└── reporter.py       Enrichment report generator
```
