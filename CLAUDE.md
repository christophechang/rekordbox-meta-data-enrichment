Python 3.12 CLI enriching Rekordbox XML with Beatport (primary), Discogs, MusicBrainz, and optional LLM disambiguation. Source of truth: `pyproject.toml`. Package: `src/enricher/`. Tests: `tests/`.

Preserve CLI flags, output semantics, and enrichment statuses unless explicitly asked to change them. Full type annotations, `mypy --strict`. `from __future__ import annotations` in all modules. Follow existing patterns: Pydantic models, async HTTP, pytest/respx mocks. External API access stays in lookup/disambiguation layers. No hardcoded secrets — env vars only.

Engine invariants:
- Fill-blank-only: only `Label`/`Year`/`Remixer` are ever proposed or written, only into currently-empty attrs (`enricher.py::_ENRICHABLE`, `writer.py::_ENRICHABLE_FIELDS`/`_PROTECTED_ATTRS`). `Name`/`Artist`/`Comments` are untouchable invariants.
- Completeness (`Label` and `Year` both present) is checked before the cache, every run — a manual correction is never re-clobbered.
- Cache (`cache.py`) stores candidates, not decisions — recomputed every run. Empty candidates and API errors are never cached (load-bearing asymmetry, retried next run); v1 files migrate automatically; corrupt cache files back up and rebuild rather than crash.
- Source order for `--sources all` (default): Beatport → Discogs → MusicBrainz, each gated on the previous not yet producing a confident+labelled match. `--sources both` = legacy MusicBrainz+Discogs only.
- Statuses (`models.py::EnrichmentDecision.status`): `enriched`, `skipped_already_complete`, `skipped_low_confidence`, `skipped_no_match`, `skipped_api_error` — the last two are never cached; one bad track never aborts a run (per-track try/except in `__main__.py::run`).

Critical files:
- `src/enricher/enricher.py` — enrichment decision flow; downstream report/writer behavior depends on statuses
- `src/enricher/writer.py` — write whitelist and the fidelity contract (diff vs source XML = blank→filled attrs + Colour, nothing else)
- `src/enricher/cache.py` — cache semantics intentionally avoid persisting some retry-worthy outcomes
- `src/enricher/__main__.py` — preserve CLI contract unless explicitly told otherwise
- API lookup changes → update/add tests for fallback query strategies and candidate extraction

Default tests: no external services. Live integration tests must be explicit, opt-in, and separate. Mock HTTP with pytest/respx. Update tests whenever behavior changes.

`ruff format . && ruff check . && mypy . && pytest`

## Releasing a version

`develop` is the integration branch — feature/fix PRs merge there. `main` holds releases.

Note `ci.yml` triggers on `main` push/PR **only**, so a PR into `develop` reports no checks. The
local check command above is the gate before merging; CI first runs when `main` is pushed. Never
tag before that run is green.

1. Merge the approved PR into `develop`.
2. Bump `version` in `pyproject.toml` (semver: patch = bugfix, minor = new capability or changed
   output/CLI contract).
3. Add a dated `CHANGELOG.md` entry at the top, grouped `Added` / `Changed` / `Fixed`.
4. Run `ruff format . && ruff check . && mypy . && pytest` — a release never proceeds on red.
   The full suite takes ~2.5 minutes.
5. Commit `chore: prepare vX.Y.Z release` on `develop` and push.
6. Merge `develop` into `main` with `--no-ff`, message `chore: release vX.Y.Z`. Push `main` and
   wait for CI to pass on it.
7. Tag `vX.Y.Z` on **main's merge commit** (not develop's prepare commit) and push the tag.
8. `gh release create vX.Y.Z` using that version's changelog section as the notes.
9. Deploy — see below.
10. Return to `develop`.

### Deploying to the Mini

The daemon runs on the Mac Mini from its own clone, reachable from the Air at
`/Volumes/Macintosh HD-1/Users/christophechang/OpenClaw/Automations/RekordboxEnricher`. That clone
tracks `develop` (which equals `main` at release time); deploy is a `git pull` there. Verify its
`HEAD` SHA matches the release tag — the clone is separate, so a merged fix is not a deployed fix.

No restart is needed: launchd `WatchPaths` invokes the daemon single-shot per file change, so the
next export picks up new code. Editing the plist is the only thing that needs a reload.

Health check: `touch` the watched file to force a run, then read `out/launchd.log` on the Mini.
A run takes ~10 minutes over the full library. Note the sha256 guard skips a byte-identical input
(`unchanged since last run, skipping`) — to force real work, the export must actually differ.
