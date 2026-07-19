from __future__ import annotations

import json
import os
import re

import httpx

from enricher.models import CandidateMatch, DisambigProvider, TrackRecord

_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)

_SYSTEM_PROMPT = """\
You are a music metadata assistant helping to match a DJ's track against release database candidates.

Given a track with known genre, BPM, and Camelot key, and a list of candidate matches from MusicBrainz or Discogs, \
identify which candidate is most likely the correct release.

Respond ONLY with a JSON object: {"index": <0-based integer or -1 if uncertain>}

Return -1 if you cannot determine a confident match. Do not guess.\
"""


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _build_prompt(track: TrackRecord, candidates: list[CandidateMatch]) -> str:
    lines = [
        f"Track: {track.artist} — {track.name}",
        f"Genre: {track.genre} | BPM: {track.bpm} | Key: {track.tonality} | Duration: {track.duration_seconds}s",
        "",
        "Candidates:",
    ]
    for i, c in enumerate(candidates):
        lines.append(
            f"  [{i}] {c.artist} — {c.title} | Label: {c.label} | Year: {c.year} | "
            f"Remixer: {c.remixer} | Duration: {c.duration_seconds or '?'}s | Source: {c.source}"
        )
    return "\n".join(lines)


def _parse_index(raw: str, num_candidates: int) -> int | None:
    """None = unparseable or out-of-range (try the next provider). -1 = model said uncertain (stop)."""
    raw = _strip_thinking(raw).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    try:
        data = json.loads(raw.strip())
        idx = int(data["index"])
    except Exception:
        return None
    if idx == -1 or 0 <= idx < num_candidates:
        return idx
    return None


async def _call_openai_compat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    path: str = "/v1/chat/completions",
    timeout: int = 30,
    extra_headers: dict[str, str] | None = None,
) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url.rstrip('/')}{path}", headers=headers, json=payload)
        resp.raise_for_status()
        return _strip_thinking(str(resp.json()["choices"][0]["message"]["content"]))


async def _try_mistral(prompt: str) -> str | None:
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        return None
    try:
        return await _call_openai_compat(
            "https://api.mistral.ai",
            api_key,
            "mistral-small-latest",
            prompt,
            timeout=30,
        )
    except Exception:
        return None


async def _try_groq(prompt: str) -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return None
    try:
        return await _call_openai_compat(
            "https://api.groq.com/openai",
            api_key,
            "llama-3.3-70b-versatile",
            prompt,
            timeout=30,
        )
    except Exception:
        return None


async def _try_gemini(prompt: str) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    try:
        return await _call_openai_compat(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            api_key,
            "gemini-2.5-flash",
            prompt,
            timeout=30,
        )
    except Exception:
        return None


async def _try_openrouter(prompt: str, model: str) -> str | None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    try:
        return await _call_openai_compat(
            "https://openrouter.ai/api",
            api_key,
            model,
            prompt,
            extra_headers={"HTTP-Referer": "https://github.com/christophechang/rekordbox-meta-data-enrichment"},
            timeout=30,
        )
    except Exception:
        return None


async def disambiguate(
    track: TrackRecord,
    candidates: list[CandidateMatch],
) -> tuple[int, DisambigProvider | None]:
    """Return (chosen_index, provider_name) or (-1, None) if unresolved."""
    if not candidates:
        return -1, None

    prompt = _build_prompt(track, candidates)
    providers: list[tuple[DisambigProvider, str | None]] = [
        ("mistral", None),
        ("groq", None),
        ("gemini", None),
        ("openrouter", "openrouter/auto"),
        ("openrouter", "mistralai/mistral-small"),
    ]
    for name, openrouter_model in providers:
        if name == "mistral":
            raw = await _try_mistral(prompt)
        elif name == "groq":
            raw = await _try_groq(prompt)
        elif name == "gemini":
            raw = await _try_gemini(prompt)
        else:
            assert openrouter_model is not None  # noqa: S101 — table above guarantees it
            raw = await _try_openrouter(prompt, openrouter_model)
        if raw is None:
            continue
        idx = _parse_index(raw, len(candidates))
        if idx is None:
            continue  # provider answered garbage — fall through, don't terminate
        return idx, name
    return -1, None
