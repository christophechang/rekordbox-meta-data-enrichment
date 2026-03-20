<<<<<<< HEAD
# rekordbox-meta-data-enrichment
Tool to enrich Rekordbox xml export files with meta data
=======
# Rekordbox Metadata Enrichment

Enriches a Rekordbox XML library export with release metadata (label, year, remixer, album, mix) sourced from MusicBrainz and Discogs. Outputs a delta XML containing only updated tracks, ready to re-import into Rekordbox.

---

## What it does

- Reads your Rekordbox XML export (`File > Export Collection in xml format`)
- Looks up each track against MusicBrainz (primary) and Discogs (secondary)
- Uses LLM disambiguation (MiniMax → Groq → Gemini cascade) for ambiguous matches
- Writes an enriched XML containing only the tracks that changed
- Colour-codes tracks in Rekordbox by match confidence for easy review

**Fields enriched:** `Label`, `Year`, `Remixer`, `Album`, `Mix`

**Fields never touched:** `Name`, `Artist`, `Genre`, `AverageBpm`, `Tonality`, `Comments`, `TotalTime`

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

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and populate your API keys:

```bash
cp .env.example .env
```

```
# LLM disambiguation — provider cascade (first available is used)
MINIMAX_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=

# Discogs — personal access token (discogs.com → Settings → Developers)
# Optional but recommended: raises rate limit from 25 to 60 req/min
DISCOGS_TOKEN=
```

At least one LLM key is required for disambiguation. MiniMax is preferred (flat-fee subscription). Groq and Gemini have free tiers.

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

1. **MiniMax** (preferred — flat-fee subscription)
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
├── disambiguator.py  LLM disambiguation (MiniMax → Groq → Gemini cascade)
├── enricher.py       Per-track pipeline orchestration
├── cache.py          Persistent JSON cache
├── writer.py         Enriched delta XML output
└── reporter.py       Enrichment report generator
```
>>>>>>> 884f47a (Initial commit — Rekordbox Metadata Enrichment CLI)
