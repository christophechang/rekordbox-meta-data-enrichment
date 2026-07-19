# Audit Mode — Design Spec

Date: 2026-07-19
Status: awaiting user review
Repo: RekordboxMetaDataEnrichment (engine — new `--audit` mode)

## 1. Context and goal

The engine and daemon fill *blank* Label/Year (fill-blank-only, never overwrite). But a large part of the library came from torrents / Soulseek, where existing Label/Year is often wrong (mislabelled scene rips, wrong pressing year, "Various Artists" junk), and some existing values were written by the *old, buggy* version of this tool (repress-year bug, label-borrow quirk). Wrong Label/Year actively degrades MixLab — a track mis-tagged with the wrong year lands in the wrong era bucket (corrupting era-coherence scoring and the era-dialogue concept direction); a wrong label misgroups it in label-coherence.

**Goal:** a **one-off, opt-in** pass that re-derives Label/Year across the *whole* collection (not just blanks), **overwrites where the source authoritatively disagrees and the match is high-confidence**, flags the uncertain disagreements for manual review, and delivers the result as change-typed Rekordbox playlists + a full diff — reviewed and imported the same way as every other run.

This is a deliberate, scoped exception to fill-blank-only. It is **not** part of the daemon.

## 2. Decisions (locked with operator)

- **Opt-in, one-off, standalone** — a CLI flag `--audit`. The daemon **never** passes it and stays fill-blank-only forever. The two risk postures never mix.
- **Whole collection** — bypass the already-complete short-circuit; look up every eligible track (same reader exclusions as always: SoundCloud/demo/blank-artist).
- **Confidence-gated writes** — the safety core:
  - **High-confidence match (green, ≥ the auto threshold) + value genuinely differs → overwrite.** We are sure it is the same track and the source is authoritative.
  - **Uncertain match (orange/red) + value differs → do NOT overwrite.** Flag it for manual review instead. A shaky match must never replace existing data.
  - **Blank field + any confident value → fill** (identical to normal enrichment).
- **Fields: Label + Year.** Remixer stays fill-only (blank→value), never overwritten — low stakes. **Artist / Title / Comments are never touched** (unchanged invariant — protects MixLab's played/unplayed match and the MIK + mood-tag `Comments` contract).
- **Label comparison is normalized** — strip `Records/Recordings/Music/Ltd`, case, punctuation, `&`/`and` before deciding "different". A spelling variant (`Sofa Sound` vs `Sofa Sound Bristol`) is **not** a discrepancy and is left alone — the audit hunts genuine mistakes, not spelling churn.
- **Year uses the existing rule uniformly** — remix designator → the remix's year; otherwise the earliest release year (MB `first-release-date` / Discogs master). Year is the murkier column (pressing-vs-original intent), so year overwrites are surfaced prominently for review; the `changes.json` diff is the per-track revert record.
- **Review UX = playlists, not a text report.** Segment changes into named Rekordbox playlists so review happens in Rekordbox (select → hear → see columns → fix in place).
- **Colour = confidence, within each playlist** (green/orange/red, as already established). No separate confidence playlists.

## 3. Behaviour

Run: `enricher --input <xml> --audit [--sources all]` (plus the usual flags). `--audit` changes three things versus a normal run; everything else (lookup, scoring, disambiguation, caching, delivery) is unchanged.

1. **No already-complete skip.** Every eligible track is looked up (cache still applies — warm from the backfill).
2. **Overwrite decision per field** (Label, Year), per track, after scoring the candidates:
   - Determine the best candidate and its confidence tier (green/orange/red), exactly as today.
   - For each of Label / Year:
     - track blank → **fill** (if the candidate has a value).
     - track has a value, source value present and **genuinely differs** (normalized for label; rule-derived for year):
       - green tier → **overwrite** (record action = `overwritten`).
       - orange/red tier → **flag** (action = `flagged`, value **not** written).
     - values match (normalized) → no change.
3. **A track's overall Colour** is set from its best-match confidence tier (unchanged), for the fills and the overwrites. Flagged-only tracks (no confident change) keep their existing colour untouched.

## 4. Output — change-typed playlists

The writer already emits named playlists; audit mode emits this set (a track appears in each playlist that applies to it — a Label+Year overwrite lands in both, so there is **no combinatorial explosion**):

- **`Audit — Label overwritten`** — existing label replaced (green writes only).
- **`Audit — Year overwritten`** — existing year replaced (green writes only).
- **`Audit — review (source disagrees)`** — uncertain (orange/red) discrepancies that were **not** written; the track keeps its existing metadata. This playlist *locates* them; the `changes.json` carries the your-value-vs-suggested detail (Rekordbox has no native field to show a suggestion, and `Comments` is untouchable).
- **`Enriched — filled blanks`** — the ordinary blank-fills (Label/Year/Remixer), same as a normal run.

Within every playlist, track **Colour** encodes confidence at a glance (green = trust, orange/red = scrutinise). The operator imports the overwrite + enriched playlists to apply changes; the review playlist is for manual inspection (no useful import — metadata is unchanged there).

## 5. changes.json (the diff + revert record)

Extend each change entry with an `action` field: `filled` | `overwritten` | `flagged`. So the artifact is the full audit trail — for every track: field, your old value, source new/suggested value, source, confidence, and what was done. This is the revert reference (Time Machine covers the audio files, not the Rekordbox metadata, so the diff is the practical undo map).

## 6. Delivery

Identical to every other run: `enrichment-import.xml` (or an audit-named output) + `changes.json` → written to the output dir, delivered to Discord (summary + attached XML) and pulled to the Air. Because `--audit` is a manual one-off, it is run by hand (or a one-shot invocation), not via the launchd WatchPaths daemon.

## 7. Invariants (enforced, tested)

- Artist / Title / Comments attributes are **never** modified (unchanged from the base engine).
- A field is overwritten **only** on a high-confidence (green) match with a genuinely different value; orange/red never overwrite.
- Label "difference" is decided on **normalized** strings — spelling variants never trigger an overwrite.
- Source XML is never modified; all outputs are separate files.
- `--audit` is inert unless explicitly passed; the daemon never passes it.

## 8. Scope

- Fields audited: Label, Year. Remixer fill-only. Nothing else.
- One-off manual operation. No daemon wiring, no scheduling.

## Out of scope

- Label canonicalisation for consistency (rewriting every label to one source's spelling) — the audit fixes *mistakes*, not spelling variants; canonicalisation is a separable future concern.
- Overwriting Remixer, Album, Genre, or any Comments-derived data.
- Auto-import / direct Rekordbox DB writes — output stays an XML the operator imports.

## 9. Testing

Existing pytest/respx style; no live calls by default.

- Overwrite gate: green + differing value → overwritten; orange/red + differing value → flagged, not written; blank → filled.
- Label normalization: `Sofa Sound` vs `Sofa Sound Bristol` (and `X` vs `X Records`) → **not** a discrepancy (no overwrite, no flag).
- Year rule under audit: remix keeps remix year; original resolves to earliest; a differing existing year on a green match → overwritten and listed in `Year overwritten`.
- Playlist routing: a Label+Year overwrite appears in both overwrite playlists; a flagged track appears only in the review playlist with metadata unchanged.
- `changes.json` `action` values correct (filled / overwritten / flagged).
- Invariant tests: Artist/Title/Comments byte-identical; `--audit` off → behaviour identical to today (fill-blank-only, no overwrites).

## 10. Open items

- Confirm year overwrite policy: default is green-overwrite (uniform rule) with prominent review; alternative is year→review-only (never auto-overwrite) if the operator wants to be maximally conservative. Default assumed: green-overwrite.
- Output naming: reuse `enrichment-import.xml` or a distinct `audit-import.xml` (avoids confusing an audit run's artifact with a routine one).
