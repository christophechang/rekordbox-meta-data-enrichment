from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from enricher.disambiguator import disambiguate
from enricher.models import CandidateMatch, TrackRecord

_TRACK = TrackRecord(
    track_id="1",
    name="Some Track",
    artist="DJ Example",
    genre="House",
    bpm=125.0,
    tonality="4A",
    duration_seconds=360,
)

_CANDIDATES = [
    CandidateMatch(
        source="musicbrainz",
        source_id="a",
        artist="DJ Example",
        title="Some Track",
        label="Defected",
        year="2021",
        confidence=0.72,
    ),
    CandidateMatch(
        source="discogs",
        source_id="b",
        artist="DJ Example",
        title="Some Track (Club Mix)",
        label="Rekids",
        year="2020",
        confidence=0.70,
    ),
]


def _llm_ok_response(index: int) -> Response:
    body = {"choices": [{"message": {"content": json.dumps({"index": index})}}]}
    return Response(200, content=json.dumps(body).encode())


def _llm_uncertain_response() -> Response:
    body = {"choices": [{"message": {"content": json.dumps({"index": -1})}}]}
    return Response(200, content=json.dumps(body).encode())


@pytest.mark.asyncio
@respx.mock
async def test_disambiguate_returns_mistral_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    respx.post("https://api.mistral.ai/v1/chat/completions").mock(return_value=_llm_ok_response(0))
    idx, provider = await disambiguate(_TRACK, _CANDIDATES)
    assert idx == 0
    assert provider == "mistral"


@pytest.mark.asyncio
@respx.mock
async def test_disambiguate_returns_minus1_when_uncertain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    respx.post("https://api.mistral.ai/v1/chat/completions").mock(return_value=_llm_uncertain_response())
    idx, provider = await disambiguate(_TRACK, _CANDIDATES)
    assert idx == -1


@pytest.mark.asyncio
@respx.mock
async def test_disambiguate_falls_back_to_groq_when_mistral_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(return_value=_llm_ok_response(1))
    idx, provider = await disambiguate(_TRACK, _CANDIDATES)
    assert idx == 1
    assert provider == "groq"


@pytest.mark.asyncio
async def test_disambiguate_returns_minus1_when_no_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    idx, provider = await disambiguate(_TRACK, _CANDIDATES)
    assert idx == -1
    assert provider is None


@pytest.mark.asyncio
async def test_disambiguate_returns_minus1_for_empty_candidates() -> None:
    idx, provider = await disambiguate(_TRACK, [])
    assert idx == -1
    assert provider is None
