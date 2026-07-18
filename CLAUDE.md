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
