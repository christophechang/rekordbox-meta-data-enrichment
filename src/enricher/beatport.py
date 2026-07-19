from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import TypedDict
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from enricher.lookup import SourceLookupError, _clean_title, _primary_artist, _strip_mix_designators
from enricher.models import CandidateMatch, TrackRecord

_BP_BASE = "https://api.beatport.com/v4"
_BP_DOCS_URL = f"{_BP_BASE}/docs/"
_BP_LOGIN_URL = f"{_BP_BASE}/auth/login/"
_BP_AUTHORIZE_URL = f"{_BP_BASE}/auth/o/authorize/"
_BP_TOKEN_URL = f"{_BP_BASE}/auth/o/token/"
_BP_REDIRECT_URI = f"{_BP_BASE}/auth/o/post-message/"
_BP_DELAY = 0.5  # conservative; no published public rate limit
_MAX_CANDIDATES = 5

# Beatport does not grant individuals OAuth client_credentials. The working flow
# (ported from TuneFinder) drives a PKCE authorization-code grant with a real
# account username/password against the internal API. See _get_token below.
_EXPIRY_MARGIN_S = 300
_NO_CREDS_MSG = "no credentials (set BEATPORT_USERNAME and BEATPORT_PASSWORD)"
_BP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_AUTH_HEADERS: dict[str, str] = {
    "User-Agent": _BP_UA,
    "Accept": "application/json",
    "Origin": "https://www.beatport.com",
    "Referer": _BP_DOCS_URL,
}
# The client_id rotates, so it is scraped from the docs page rather than hardcoded.
# Case-insensitive: the live bundles expose it as `CLIENT_ID: '...'` (uppercase).
_CLIENT_ID_RE = re.compile(r"""client[_]?id["']?\s*[:=]\s*["']([A-Za-z0-9]{20,})["']""", re.IGNORECASE)
_SCRIPT_SRC_RE = re.compile(r"""<script[^>]+src=["']([^"']+)["']""")

# Token cache: a chmod-600 dotfile at the cwd root, alongside .enrichment_cache.json.
_TOKEN_CACHE_FILE = Path(".beatport_token.json")

_BP_SEMAPHORE: asyncio.Semaphore | None = None


class _TokenRecord(TypedDict):
    access_token: str
    refresh_token: str
    expires_at: float
    obtained_at: float


# In-process cache in front of the file, so a valid token never triggers a
# re-login within a single run (tracks are processed sequentially).
_token_cache: _TokenRecord | None = None


def _get_bp_semaphore() -> asyncio.Semaphore:
    global _BP_SEMAPHORE
    if _BP_SEMAPHORE is None:
        _BP_SEMAPHORE = asyncio.Semaphore(1)
    return _BP_SEMAPHORE


# ---------------------------------------------------------------------------
# Token cache file (structural validation on load; atomic + chmod-600 on save).
# ---------------------------------------------------------------------------


def _load_cache() -> _TokenRecord | None:
    try:
        raw = _TOKEN_CACHE_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data: object = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    access = data.get("access_token")
    refresh = data.get("refresh_token", "")
    expires_at = data.get("expires_at")
    obtained_at = data.get("obtained_at", 0.0)
    if not isinstance(access, str) or not access:
        return None
    if not isinstance(refresh, str):
        return None
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
        return None
    if not isinstance(obtained_at, (int, float)) or isinstance(obtained_at, bool):
        obtained_at = 0.0
    return _TokenRecord(
        access_token=access,
        refresh_token=refresh,
        expires_at=float(expires_at),
        obtained_at=float(obtained_at),
    )


def _save_cache(record: _TokenRecord) -> None:
    # Best-effort: a persistence failure must not sink a token we already hold.
    # Create the tmp file 0o600 from the start (never a world-readable window), and
    # remove it if the swap fails so a tokens-bearing .tmp is never left behind.
    tmp = _TOKEN_CACHE_FILE.with_name(f"{_TOKEN_CACHE_FILE.name}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(record).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, _TOKEN_CACHE_FILE)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _invalidate_cache() -> None:
    global _token_cache
    _token_cache = None
    with contextlib.suppress(OSError):
        _TOKEN_CACHE_FILE.unlink()


# ---------------------------------------------------------------------------
# PKCE authorization-code login flow (async httpx port of the TuneFinder flow).
# ---------------------------------------------------------------------------


def _match_client_id(text: str) -> str | None:
    m = _CLIENT_ID_RE.search(text)
    return m.group(1) if m else None


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _record_from_token_response(payload: object, old_refresh: str) -> _TokenRecord:
    if not isinstance(payload, dict):
        raise ValueError("token response is not a JSON object")
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise ValueError("token response missing access_token")
    refresh_raw = payload.get("refresh_token")
    refresh = refresh_raw if isinstance(refresh_raw, str) and refresh_raw else old_refresh
    expires_in = payload.get("expires_in")
    ttl = float(expires_in) if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) else 3600.0
    now = time.time()
    return _TokenRecord(
        access_token=access,
        refresh_token=refresh,
        expires_at=now + ttl,
        obtained_at=now,
    )


async def _scrape_client_id(client: httpx.AsyncClient) -> str:
    resp = await client.get(_BP_DOCS_URL)
    resp.raise_for_status()
    html = resp.text
    found = _match_client_id(html)
    if found:
        return found
    for src in _SCRIPT_SRC_RE.findall(html):
        script_url = urljoin(str(resp.url), str(src))
        try:
            script_resp = await client.get(script_url)
            script_resp.raise_for_status()
        except httpx.HTTPError:
            continue
        found = _match_client_id(script_resp.text)
        if found:
            return found
    raise SourceLookupError("beatport", "could not scrape client_id from docs page")


async def _refresh(client: httpx.AsyncClient, client_id: str, refresh_token: str) -> _TokenRecord | None:
    try:
        resp = await client.post(
            _BP_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
        )
        resp.raise_for_status()
        return _record_from_token_response(resp.json(), refresh_token)
    except (httpx.HTTPError, ValueError):
        return None


async def _login(client: httpx.AsyncClient, username: str, password: str) -> None:
    resp = await client.post(_BP_LOGIN_URL, json={"username": username, "password": password})
    if resp.status_code not in (200, 201, 204):
        raise SourceLookupError("beatport", f"login rejected ({resp.status_code})")


async def _authorize(client: httpx.AsyncClient, client_id: str, challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _BP_REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    # Do NOT follow the redirect — the auth code is in the Location header.
    resp = await client.get(_BP_AUTHORIZE_URL, params=params, follow_redirects=False)
    location: str | None = resp.headers.get("location")
    if not location:
        raise SourceLookupError("beatport", f"authorize did not redirect ({resp.status_code})")
    codes = parse_qs(urlparse(location).query).get("code")
    if not codes or not codes[0]:
        raise SourceLookupError("beatport", "authorize redirect missing code")
    return codes[0]


async def _exchange_token(client: httpx.AsyncClient, client_id: str, code: str, verifier: str) -> _TokenRecord:
    resp = await client.post(
        _BP_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _BP_REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    resp.raise_for_status()
    try:
        return _record_from_token_response(resp.json(), "")
    except ValueError as exc:
        raise SourceLookupError("beatport", f"malformed token response: {exc}") from exc


async def _full_login(client: httpx.AsyncClient, client_id: str, username: str, password: str) -> _TokenRecord:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    await _login(client, username, password)
    code = await _authorize(client, client_id, challenge, state)
    return await _exchange_token(client, client_id, code, verifier)


async def _get_token() -> str:
    global _token_cache
    now = time.time()
    if _token_cache is not None and _token_cache["expires_at"] - now > _EXPIRY_MARGIN_S:
        return _token_cache["access_token"]

    record = _load_cache()
    if record is not None and record["expires_at"] - now > _EXPIRY_MARGIN_S:
        _token_cache = record
        return record["access_token"]

    refresh_token = record["refresh_token"] if record is not None else ""
    username = os.environ.get("BEATPORT_USERNAME", "")
    password = os.environ.get("BEATPORT_PASSWORD", "")
    have_creds = bool(username) and bool(password)
    if not refresh_token and not have_creds:
        raise SourceLookupError("beatport", _NO_CREDS_MSG)

    # One client across login -> authorize so session cookies persist (httpx keeps
    # cookies on a client by default).
    try:
        async with httpx.AsyncClient(timeout=20, headers=_AUTH_HEADERS) as client:
            client_id = await _scrape_client_id(client)
            new_record: _TokenRecord | None = None
            if refresh_token:
                new_record = await _refresh(client, client_id, refresh_token)
            if new_record is None:
                if not have_creds:
                    raise SourceLookupError("beatport", _NO_CREDS_MSG)
                new_record = await _full_login(client, client_id, username, password)
    except httpx.HTTPError as exc:
        raise SourceLookupError("beatport", f"auth failed: {exc}") from exc

    _token_cache = new_record
    _save_cache(new_record)
    return new_record["access_token"]


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
                    _invalidate_cache()
                    raise SourceLookupError("beatport", "auth rejected (401) — token expired or invalid")
                resp.raise_for_status()
                data: dict[str, object] = resp.json()
                return _extract_bp_candidates(data)
        except httpx.HTTPError as exc:
            raise SourceLookupError("beatport", str(exc)) from exc
