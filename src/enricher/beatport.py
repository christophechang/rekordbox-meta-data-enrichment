from __future__ import annotations

import asyncio
import os

import httpx

from enricher.lookup import SourceLookupError, _clean_title, _primary_artist, _strip_mix_designators
from enricher.models import CandidateMatch, TrackRecord

_BP_BASE = "https://api.beatport.com/v4"
_BP_TOKEN_URL = "https://api.beatport.com/v4/auth/o/token/"
_BP_DELAY = 0.5  # conservative; no published public rate limit
_MAX_CANDIDATES = 5

_BP_SEMAPHORE: asyncio.Semaphore | None = None
_token_cache: dict[str, str] = {}


def _get_bp_semaphore() -> asyncio.Semaphore:
    global _BP_SEMAPHORE
    if _BP_SEMAPHORE is None:
        _BP_SEMAPHORE = asyncio.Semaphore(1)
    return _BP_SEMAPHORE


async def _get_token() -> str:
    static = os.environ.get("BEATPORT_API_TOKEN", "")
    if static:
        return static
    cached = _token_cache.get("access_token")
    if cached:
        return cached
    cid = os.environ.get("BEATPORT_CLIENT_ID", "")
    secret = os.environ.get("BEATPORT_CLIENT_SECRET", "")
    if not (cid and secret):
        raise SourceLookupError("beatport", "no credentials (set BEATPORT_API_TOKEN or BEATPORT_CLIENT_ID/SECRET)")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(_BP_TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(cid, secret))
            resp.raise_for_status()
            token = str(resp.json()["access_token"])
    except httpx.HTTPError as exc:
        raise SourceLookupError("beatport", f"token request failed: {exc}") from exc
    _token_cache["access_token"] = token
    return token


def _extract_bp_candidates(data: dict[str, object]) -> list[CandidateMatch]:
    results = data.get("results", [])
    out: list[CandidateMatch] = []
    if not isinstance(results, list):
        return out
    for t in results[:_MAX_CANDIDATES]:
        if not isinstance(t, dict):
            continue
        artists = t.get("artists", [])
        artist = (
            ", ".join(str(a.get("name", "")) for a in artists if isinstance(a, dict))
            if isinstance(artists, list)
            else ""
        )
        release = t.get("release") if isinstance(t.get("release"), dict) else {}
        label_obj = release.get("label") if isinstance(release, dict) and isinstance(release.get("label"), dict) else {}
        remixers = t.get("remixers", [])
        remixer = (
            str(remixers[0].get("name", ""))
            if isinstance(remixers, list) and remixers and isinstance(remixers[0], dict)
            else ""
        )
        length_ms = t.get("length_ms")
        publish = str(t.get("publish_date", "") or "")
        mix_name = str(t.get("mix_name", "") or "")
        name = str(t.get("name", "") or "")
        title = f"{name} ({mix_name})" if mix_name and mix_name.lower() not in name.lower() else name
        out.append(
            CandidateMatch(
                source="beatport",
                source_id=str(t.get("id", "")),
                artist=artist,
                title=title,
                label=str(label_obj.get("name", "")) if isinstance(label_obj, dict) else "",
                year=publish[:4],
                remixer=remixer,
                album=str(release.get("name", "")) if isinstance(release, dict) else "",
                mix=mix_name,
                duration_seconds=int(length_ms) // 1000 if isinstance(length_ms, int) else None,
            )
        )
    # Year rule: no remix designator → earliest release year wins. Sort ascending by
    # year with blanks last so that when score_all later finds two candidates tied on
    # confidence (identical name+mix+artist — e.g. an original vs. a Beatport reissue),
    # its stable sort preserves this order and the EARLIEST Beatport publish year is
    # the one that ends up first, i.e. the winner.
    return sorted(out, key=lambda c: (c.year == "", c.year))


async def lookup_beatport(track: TrackRecord) -> list[CandidateMatch]:
    token = await _get_token()
    clean = _clean_title(track.name)
    params = {
        "name": _strip_mix_designators(clean),
        "artist_name": _primary_artist(track.artist),
        "per_page": str(_MAX_CANDIDATES),
    }
    async with _get_bp_semaphore():
        await asyncio.sleep(_BP_DELAY)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{_BP_BASE}/catalog/tracks/", params=params, headers={"Authorization": f"Bearer {token}"}
                )
                if resp.status_code == 401:
                    _token_cache.clear()
                    raise SourceLookupError("beatport", "auth rejected (401) — token expired or invalid")
                resp.raise_for_status()
                data: dict[str, object] = resp.json()
                return _extract_bp_candidates(data)
        except httpx.HTTPError as exc:
            raise SourceLookupError("beatport", str(exc)) from exc
