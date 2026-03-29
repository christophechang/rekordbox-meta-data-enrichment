# CLAUDE.md

## Purpose
- This repository is a Python 3.12 CLI for enriching Rekordbox XML exports with metadata from MusicBrainz, Discogs, and optional LLM disambiguation.
- Keep changes small, explicit, and easy to review.

## Source Of Truth
- Treat `pyproject.toml` as the source of truth for formatting, linting, typing, and pytest configuration.
- Follow the existing package layout under `src/enricher/` and tests under `tests/`.

## Working Style
- Prefer minimal, incremental changes over refactors.
- Do not expand scope beyond the requested task.
- Preserve existing CLI flags, output semantics, and enrichment statuses unless the task explicitly requires behavioral change.
- Avoid adding dependencies without approval.

## Code Expectations
- Keep full type annotations and stay compatible with `mypy --strict`.
- Use `from __future__ import annotations` in Python modules.
- Follow existing patterns for Pydantic models, async HTTP usage, and pytest structure.
- Keep external API access isolated to the lookup and disambiguation layers unless there is a clear reason not to.
- Do not hardcode secrets; load them from environment variables.

## Repo-Specific Guardrails
- Be careful when changing enrichment decision flow in `src/enricher/enricher.py`; downstream report and writer behavior depends on those statuses.
- Be careful when changing cache behavior in `src/enricher/cache.py`; current semantics intentionally avoid persisting some retry-worthy outcomes.
- Preserve the current CLI contract in `src/enricher/__main__.py` unless the user explicitly asks to change it.
- When changing API lookup behavior, update or add tests for fallback query strategies and candidate extraction.

## Testing
- Default automated tests should not rely on external services.
- Live integration tests are allowed, but they should be explicit, opt-in, and separated from the default test run.
- Mock HTTP calls for unit tests using the existing pytest/respx patterns.
- Add or update tests when behavior changes.

## Validation
- Use these commands when relevant:

```bash
ruff format .
ruff check .
mypy .
pytest
```

## Commits
- Use conventional commits.
- Do not add `Co-Authored-By` trailers.
