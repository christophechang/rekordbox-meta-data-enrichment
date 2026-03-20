from __future__ import annotations

import asyncio
import re

import httpx

from enricher.models import CandidateMatch, TrackRecord

_MB_BASE = "https://musicbrainz.org/ws/2"
_DISCOGS_BASE = "https://api.discogs.com"
_USER_AGENT = "RekordboxEnricher/0.1 ( https://github.com/christophechang/rekordbox-meta-data-enrichment )"

_MB_SEMAPHORE: asyncio.Semaphore | None = None
_DISCOGS_SEMAPHORE: asyncio.Semaphore | None = None

_MAX_CANDIDATES = 5

# MusicBrainz: 3 req/sec with User-Agent header — stay comfortably under
_MB_DELAY = 0.34  # ~2.9 req/sec

# Discogs: 60 req/min authenticated, 25 req/min unauthenticated
# Semaphore(1) serialises requests so each delay is meaningful (no burst)
_DISCOGS_DELAY_AUTHED = 1.0  # 60 req/min with personal access token
_DISCOGS_DELAY_UNAUTHED = 2.5  # 24 req/min without token (safe under 25/min limit)


def _get_mb_semaphore() -> asyncio.Semaphore:
    global _MB_SEMAPHORE
    if _MB_SEMAPHORE is None:
        _MB_SEMAPHORE = asyncio.Semaphore(1)
    return _MB_SEMAPHORE


def _get_discogs_semaphore() -> asyncio.Semaphore:
    global _DISCOGS_SEMAPHORE
    if _DISCOGS_SEMAPHORE is None:
        _DISCOGS_SEMAPHORE = asyncio.Semaphore(1)
    return _DISCOGS_SEMAPHORE


# Matches trailing BPM/key info appended to titles, e.g. "Track Title 128", "Mix (Extended) 1B 137"
_TRAILING_BPM_RE = re.compile(r"\s+\d{1,3}[AB]?(\s+\d{2,3})?\s*$")

# Matches catalogue numbers in brackets/parens, e.g. "[HTH115]", "(BTB002)"
_CATALOGUE_RE = re.compile(r"[\[\(][A-Z]{2,}[-\s]?\d+[\]\)]")

# Matches common mix/version/feat designators that DBs often omit from track listings
_MIX_DESIGNATOR_RE = re.compile(
    r"\s*[\[\(]"
    r"(?:Original Mix|Extended Mix|Extended|Club Mix|VIP Mix|Dub Mix|Dub|Instrumental|"
    r"Radio Edit|Radio Mix|Album Version|Single Version|\d{4}\s+Remaster(?:ed)?|"
    r"feat\.[^)\]]*|ft\.[^)\]]*|with\s+[^)\]]*|Featuring\s+[^)\]]*)"
    r"[\]\)]\s*",
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    """Strip known query-breaking artefacts from a title before sending to APIs."""
    title = _CATALOGUE_RE.sub("", title)
    title = _TRAILING_BPM_RE.sub("", title)
    return title.strip()


def _strip_mix_designators(title: str) -> str:
    """Remove mix/version/feat qualifiers — DBs often index the bare track name."""
    return _MIX_DESIGNATOR_RE.sub("", title).strip()


def _primary_artist(artist: str) -> str:
    """Return the first-credited artist from a multi-artist string."""
    for sep in ("/", ",", " & ", " x ", " vs ", " vs. "):
        if sep in artist:
            return artist.split(sep)[0].strip()
    return artist


def _mb_headers() -> dict[str, str]:
    return {"User-Agent": _USER_AGENT, "Accept": "application/json"}


def _discogs_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Discogs token={token}"
    return headers


def _best_mb_release(releases: list[object]) -> dict[str, object] | None:
    """Pick the most useful release for label/year/album metadata.

    Prefers non-compilation, non-DJ-mix releases (so we get the original label rather
    than a compilation imprint), then breaks ties by earliest release date.
    """
    valid = [r for r in releases if isinstance(r, dict)]
    if not valid:
        return None

    def _is_compilation_or_dj_mix(r: dict[str, object]) -> bool:
        rg = r.get("release-group", {})
        if not isinstance(rg, dict):
            return False
        secondary = rg.get("secondary-types", [])
        return isinstance(secondary, list) and any(t in ("Compilation", "DJ-mix") for t in secondary)

    def _sort_key(r: dict[str, object]) -> tuple[int, str]:
        return (1 if _is_compilation_or_dj_mix(r) else 0, str(r.get("date", "") or "9999"))

    return min(valid, key=_sort_key)


def _extract_mb_candidates(data: dict[str, object]) -> list[CandidateMatch]:
    candidates: list[CandidateMatch] = []
    recordings = data.get("recordings", [])
    if not isinstance(recordings, list):
        return candidates

    for rec in recordings[:_MAX_CANDIDATES]:
        if not isinstance(rec, dict):
            continue
        title = str(rec.get("title", ""))
        length_ms = rec.get("length")
        duration_seconds = int(length_ms) // 1000 if isinstance(length_ms, int) else None

        # Pull artist from first credit
        artist = ""
        artist_credit = rec.get("artist-credit", [])
        if isinstance(artist_credit, list) and artist_credit:
            first = artist_credit[0]
            if isinstance(first, dict):
                artist_obj = first.get("artist", {})
                if isinstance(artist_obj, dict):
                    artist = str(artist_obj.get("name", ""))

        # Pull label, year, album from best release (prefer non-compilation, then earliest)
        label = ""
        year = ""
        album = ""
        releases = rec.get("releases", [])
        best_release = _best_mb_release(releases if isinstance(releases, list) else [])
        if best_release is not None:
            album = str(best_release.get("title", ""))
            date_raw = best_release.get("date", "")
            year = str(date_raw)[:4] if date_raw else ""
            label_info = best_release.get("label-info", [])
            if isinstance(label_info, list) and label_info:
                first_label = label_info[0]
                if isinstance(first_label, dict):
                    label_obj = first_label.get("label", {})
                    if isinstance(label_obj, dict):
                        label = str(label_obj.get("name", ""))

        # Extract remixer from relations
        remixer = ""
        relations = rec.get("relations", [])
        if isinstance(relations, list):
            for rel in relations:
                if not isinstance(rel, dict):
                    continue
                if str(rel.get("type", "")).lower() == "remixer":
                    artist_obj = rel.get("artist", {})
                    if isinstance(artist_obj, dict):
                        remixer = str(artist_obj.get("name", ""))
                        break

        candidates.append(
            CandidateMatch(
                source="musicbrainz",
                source_id=str(rec.get("id", "")),
                artist=artist,
                title=title,
                label=label,
                year=year,
                remixer=remixer,
                album=album,
                mix="",
                duration_seconds=duration_seconds,
            )
        )
    return candidates


async def _mb_query(artist: str, title: str) -> list[CandidateMatch]:
    query = f'artist:"{artist}" AND recording:"{title}"'
    params = {
        "query": query,
        "fmt": "json",
        "limit": str(_MAX_CANDIDATES),
        "inc": "releases labels artist-credits relations",
    }
    async with _get_mb_semaphore():
        await asyncio.sleep(_MB_DELAY)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{_MB_BASE}/recording/", params=params, headers=_mb_headers())
                resp.raise_for_status()
                data: dict[str, object] = resp.json()
                return _extract_mb_candidates(data)
        except Exception:
            return []


async def lookup_musicbrainz(track: TrackRecord) -> list[CandidateMatch]:
    clean_title = _clean_title(track.name)
    stripped_title = _strip_mix_designators(clean_title)
    primary = _primary_artist(track.artist)

    results = await _mb_query(track.artist, clean_title)
    if not results and primary != track.artist:
        results = await _mb_query(primary, clean_title)
    if not results and stripped_title != clean_title:
        results = await _mb_query(primary, stripped_title)
    return results


def _extract_discogs_candidates(data: dict[str, object], track_name: str) -> list[CandidateMatch]:
    candidates: list[CandidateMatch] = []
    results = data.get("results", [])
    if not isinstance(results, list):
        return candidates

    for result in results[:_MAX_CANDIDATES]:
        if not isinstance(result, dict):
            continue
        title_raw = str(result.get("title", ""))
        # Discogs title format: "Artist - Title" or just "Title"
        if " - " in title_raw:
            parts = title_raw.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = ""
            title = title_raw

        year_raw = result.get("year", "")
        year = str(year_raw) if year_raw else ""
        label_list = result.get("label", [])
        label = str(label_list[0]) if isinstance(label_list, list) and label_list else ""
        style_list = result.get("style", [])
        styles = [str(s) for s in style_list] if isinstance(style_list, list) else []

        candidates.append(
            CandidateMatch(
                source="discogs",
                source_id=str(result.get("id", "")),
                artist=artist,
                title=title if title else track_name,
                label=label,
                year=year,
                remixer="",
                album=str(result.get("title", "")),
                mix="",
                styles=styles,
                duration_seconds=None,
            )
        )
    return candidates


async def _discogs_query(artist: str, title: str, track_name: str, token: str | None) -> list[CandidateMatch]:
    params: dict[str, str] = {
        "artist": artist,
        "track": title,
        "type": "release",
        "per_page": str(_MAX_CANDIDATES),
    }
    delay = _DISCOGS_DELAY_AUTHED if token else _DISCOGS_DELAY_UNAUTHED
    async with _get_discogs_semaphore():
        await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{_DISCOGS_BASE}/database/search", params=params, headers=_discogs_headers(token)
                )
                resp.raise_for_status()
                ddata: dict[str, object] = resp.json()
                return _extract_discogs_candidates(ddata, track_name)
        except Exception:
            return []


async def lookup_discogs(track: TrackRecord, token: str | None = None) -> list[CandidateMatch]:
    clean_title = _clean_title(track.name)
    stripped_title = _strip_mix_designators(clean_title)
    primary = _primary_artist(track.artist)

    results = await _discogs_query(track.artist, clean_title, track.name, token)
    if not results and primary != track.artist:
        results = await _discogs_query(primary, clean_title, track.name, token)
    if not results and stripped_title != clean_title:
        results = await _discogs_query(primary, stripped_title, track.name, token)
    return results
