from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from factories import _SOURCE_XML

from enricher.__main__ import run


def _make_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "input": Path("import/rekordbox.xml"),
        "output": Path("out.xml"),
        "report": None,
        "cache": Path(".enrichment_cache.json"),
        "dry_run": False,
        "confidence_threshold": 0.85,
        "sources": "all",
        "no_llm": True,
        "limit": None,
        "no_cache": True,
        "no_colour_confidence": False,
        "full_export": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


async def test_run_survives_per_track_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    xml = tmp_path / "in.xml"
    xml.write_text(_SOURCE_XML, encoding="utf-8")  # from tests/factories.py (Task 2)

    async def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr("enricher.__main__.process_track", _boom)
    args = _make_args(input=xml, output=tmp_path / "out.xml", no_cache=True, no_llm=True)
    await run(args)  # must not raise
    captured = capsys.readouterr()
    assert "unexpected failure" in captured.err
    assert "Skipped (API error)    : 2" in captured.out


async def test_llm_enabled_without_keys_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ("MISTRAL_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    xml = tmp_path / "in.xml"
    xml.write_text(_SOURCE_XML, encoding="utf-8")
    args = _make_args(input=xml, output=tmp_path / "out.xml", no_cache=True, no_llm=False, limit=0)
    await run(args)
    assert "no provider keys" in capsys.readouterr().err
