# Repo AGENTS.md

## Overview
- This repository contains a Python 3.12 CLI that enriches Rekordbox XML exports with release metadata.
- Core package: `src/enricher/`
- Test suite: `tests/`
- Tooling and quality rules live in `pyproject.toml`

## How To Work In This Repo
- Prefer simple, minimal changes that match existing patterns.
- Verify assumptions by reading the relevant module and tests before editing.
- Do not refactor outside the requested scope.
- Do not add dependencies, new frameworks, or broad architectural changes without approval.

## Key Files
- `src/enricher/__main__.py`: CLI argument parsing, orchestration, report/output handling
- `src/enricher/enricher.py`: per-track decision flow and status assignment
- `src/enricher/lookup.py`: MusicBrainz and Discogs lookup logic
- `src/enricher/disambiguator.py`: optional LLM-based candidate resolution
- `src/enricher/cache.py`: persistent cache behavior
- `src/enricher/writer.py`: output XML generation
- `src/enricher/reporter.py`: human-readable report generation
- `tests/`: behavior-focused pytest coverage

## Repo-Specific Expectations
- Preserve existing CLI flags and defaults unless the task explicitly changes user-facing behavior.
- Preserve current enrichment statuses and confidence behavior unless the requested change requires otherwise.
- Be careful with cache semantics; they affect repeat runs and retry behavior.
- Keep external API usage behind the existing lookup/disambiguation boundaries.
- Do not hardcode API keys or tokens.

## Testing And Verification
- Follow the repo tooling config in `pyproject.toml`.
- Relevant validation commands:

```bash
ruff format .
ruff check .
mypy .
pytest
```

- Default tests should be deterministic and should not require live external services.
- Integration tests against real providers are allowed, but they should be opt-in and not part of the default test run unless explicitly requested.
- When behavior changes, update or add tests in `tests/`.

## Change Discipline
- Prefer fixing the smallest correct surface area.
- Keep docs aligned with the actual codebase; do not document files or workflows that do not exist.
- If you find unrelated issues, note them separately rather than folding them into the same change unless asked.

## Commits
- Use conventional commits.
- Do not add `Co-Authored-By` trailers.
