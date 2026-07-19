# Changelog

All notable changes to this project are documented here. Versions before 0.6.0
are recorded only as git tags (`v0.1.0`–`v0.5.0`).

## [0.6.1] — 2026-07-19

### Fixed
- **"Updated Tracks" playlist no longer lists unchanged tracks.** In delta mode the
  playlist was built from whatever survived in the `COLLECTION`, but colour-confidence
  mode (the default) deliberately keeps every unresolved track so its stale `Colour`
  can be blanked on import. Those tracks were counted as updates — a run enriching a
  single track produced a 111-entry playlist. Membership is now derived from decision
  status, as full-export mode already did.
- **"Unable to Enrich" playlist is no longer silently dropped.** No-match tracks were
  excluded from it for already appearing in "Updated Tracks", leaving it empty and
  omitted from the output entirely. Kept-but-not-enriched tracks now land there, so
  their blanked `Colour` actually applies on import instead of sitting in no playlist.

### Changed
- Release runbook documented in `CLAUDE.md` (branch flow, tagging, Mini deploy).

## [0.6.0] — 2026-07-19

The "it actually works, and runs itself" release. A near-total engine rework for
correctness, a new primary metadata source (Beatport), and a launchd-triggered
daemon that enriches each Rekordbox export automatically.

### Added
- **Beatport as the primary metadata source** — official v4 catalogue via the
  account username/password PKCE flow (ported from TuneFinder), with a
  cached/refreshed token in a chmod-600 dotfile. Mix-aware scoring; source order
  is beatport → discogs → musicbrainz (`--sources all`, the new default).
- **Enrich-on-export daemon** (`enricher.daemon`) — a single-shot, launchd
  `WatchPaths`-triggered wrapper: it enriches each new export, writes the
  Rekordbox import-delta, and posts the summary + import file to Discord. Ships
  with a launchd plist template and Mini deploy guide under `deploy/`.
- **`changes.json`** machine-readable per-field change record (old → new, source,
  confidence, colour); winning source named in the text report.

### Changed
- **Fill-blank-only write policy** — enrichment never overwrites a non-empty
  field; only Label/Year/Remixer/Colour are ever written; Name/Artist/Comments
  are never touched. Enforced at both the decision and writer layers.
- **Cache stores raw candidates, not decisions** (schema v2, auto-migrates v1) —
  decisions are recomputed every run, so manual corrections are never
  re-clobbered and flags always take effect. Atomic writes + corrupt-file
  recovery. Empty results and API errors are never cached.
- **Mix-level year rule** — remix titles keep the remix's year; originals resolve
  to the earliest release (MusicBrainz `first-release-date` / Discogs master).

### Fixed
- **MusicBrainz** — dropped the no-op `inc=` on search (year now from
  `first-release-date`); label/remixer restored via a follow-up recording +
  release lookup (`inc=labels` is release-only — requesting it on `/recording`
  returned 400 and turned valid matches into errors); honest 1 req/s rate limit;
  HTTP failures surface instead of masquerading as "no match".
- **Beatport** — case-insensitive `client_id` scrape (the live bundle exposes it
  uppercase); token file created 0o600 from the start.
- **Discogs** — lookup errors surface as `SourceLookupError`; matched-pressing
  year resolved to the master's original year; earliest-publish tiebreak.
- **Matching** — `(Artist Remix)` handled in query stripping and scoring;
  MIK-only trailing-token strip ("Xpander 2" survives); Lucene escaping;
  artist-containment length guard; broader genre families.
- **LLM cascade** — falls through to the next provider on a parse failure;
  `openrouter/auto`; candidate durations in the prompt.
- **Robustness** — per-track containment (one bad track never aborts a run);
  daemon subprocess timeout; **daemon file-stability wait + malformed-XML skip**
  (a non-atomic export copy could be read mid-write and crash the parse).

### Notes
- 190 tests; `ruff format`/`ruff check`/`mypy --strict`/`pytest` gate.
- Live on the Mac Mini via launchd (`com.openclaw.rekordbox-enricher`), tracking
  `develop`.
