Python 3.12 CLI enriching Rekordbox XML with MusicBrainz, Discogs, and optional LLM disambiguation. Source of truth: `pyproject.toml`. Package: `src/enricher/`. Tests: `tests/`.

Preserve CLI flags, output semantics, and enrichment statuses unless explicitly asked to change them. Full type annotations, `mypy --strict`. `from __future__ import annotations` in all modules. Follow existing patterns: Pydantic models, async HTTP, pytest/respx mocks. External API access stays in lookup/disambiguation layers. No hardcoded secrets — env vars only.

Critical files:
- `src/enricher/enricher.py` — enrichment decision flow; downstream report/writer behavior depends on statuses
- `src/enricher/cache.py` — cache semantics intentionally avoid persisting some retry-worthy outcomes
- `src/enricher/__main__.py` — preserve CLI contract unless explicitly told otherwise
- API lookup changes → update/add tests for fallback query strategies and candidate extraction

Default tests: no external services. Live integration tests must be explicit, opt-in, and separate. Mock HTTP with pytest/respx. Update tests whenever behavior changes.

`ruff format . && ruff check . && mypy . && pytest`