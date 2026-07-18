from __future__ import annotations

import json
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from factories import _track

from enricher import beatport
from enricher.beatport import lookup_beatport
from enricher.lookup import SourceLookupError

# ---------------------------------------------------------------------------
# Endpoints (kept as literals so the tests double as a contract for the flow).
# ---------------------------------------------------------------------------
_DOCS_URL = "https://api.beatport.com/v4/docs/"
_LOGIN_URL = "https://api.beatport.com/v4/auth/login/"
_AUTHORIZE_URL = "https://api.beatport.com/v4/auth/o/authorize/"
_TOKEN_URL = "https://api.beatport.com/v4/auth/o/token/"
_TRACKS_URL = "https://api.beatport.com/v4/catalog/tracks/"
_POST_MESSAGE = "https://api.beatport.com/v4/auth/o/post-message/"

_CLIENT_ID = "AbCdEfGhIjKlMnOpQrSt12345"
_DOCS_HTML = f'<html><body><script>window.__cfg={{"client_id": "{_CLIENT_ID}"}};</script></body></html>'


@pytest.fixture(autouse=True)
def _bp_hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    # Hermetic: strip every Beatport env var (old + new), point the token cache at
    # a throwaway file, and reset the in-process cache/semaphore so the suite is
    # order-independent regardless of the developer's env or any real cache.
    for var in (
        "BEATPORT_API_TOKEN",
        "BEATPORT_CLIENT_ID",
        "BEATPORT_CLIENT_SECRET",
        "BEATPORT_USERNAME",
        "BEATPORT_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(beatport, "_TOKEN_CACHE_FILE", tmp_path / ".beatport_token.json")
    monkeypatch.setattr(beatport, "_token_cache", None)
    monkeypatch.setattr(beatport, "_BP_SEMAPHORE", None)
    monkeypatch.setattr(beatport, "_BP_DELAY", 0.0)
    yield


def _write_cache(**over: object) -> None:
    rec: dict[str, object] = {
        "access_token": "cached-tok",
        "refresh_token": "",
        "expires_at": time.time() + 3600,
        "obtained_at": time.time(),
    }
    rec.update(over)
    beatport._TOKEN_CACHE_FILE.write_text(json.dumps(rec), encoding="utf-8")


# ---------------------------------------------------------------------------
# Token acquisition — cache, refresh, and full PKCE login.
# ---------------------------------------------------------------------------


@respx.mock
async def test_cache_hit_returns_without_network() -> None:
    _write_cache(access_token="live-tok", expires_at=time.time() + 3600)
    token = await beatport._get_token()
    assert token == "live-tok"
    assert respx.calls.call_count == 0


@respx.mock
async def test_expired_cache_uses_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_USERNAME", "u")
    monkeypatch.setenv("BEATPORT_PASSWORD", "p")
    _write_cache(access_token="stale", refresh_token="refresh-123", expires_at=time.time() - 10)
    respx.get(_DOCS_URL).respond(text=_DOCS_HTML)
    token_route = respx.post(_TOKEN_URL).respond(
        json={"access_token": "refreshed", "refresh_token": "refresh-456", "expires_in": 3600}
    )
    token = await beatport._get_token()
    assert token == "refreshed"
    body = token_route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh-123" in body
    saved = json.loads(beatport._TOKEN_CACHE_FILE.read_text())
    assert saved["access_token"] == "refreshed"
    assert saved["refresh_token"] == "refresh-456"


@respx.mock
async def test_refresh_without_new_refresh_token_keeps_old(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_USERNAME", "u")
    monkeypatch.setenv("BEATPORT_PASSWORD", "p")
    _write_cache(access_token="stale", refresh_token="keep-me", expires_at=time.time() - 10)
    respx.get(_DOCS_URL).respond(text=_DOCS_HTML)
    respx.post(_TOKEN_URL).respond(json={"access_token": "refreshed", "expires_in": 3600})
    token = await beatport._get_token()
    assert token == "refreshed"
    saved = json.loads(beatport._TOKEN_CACHE_FILE.read_text())
    assert saved["refresh_token"] == "keep-me"


@respx.mock
async def test_refresh_failure_falls_through_to_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_USERNAME", "u")
    monkeypatch.setenv("BEATPORT_PASSWORD", "p")
    _write_cache(access_token="stale", refresh_token="dead-refresh", expires_at=time.time() - 10)
    respx.get(_DOCS_URL).respond(text=_DOCS_HTML)
    respx.post(_LOGIN_URL).respond(200, headers={"Set-Cookie": "sessionid=s; Path=/"})
    respx.get(_AUTHORIZE_URL).respond(302, headers={"Location": f"{_POST_MESSAGE}?code=CODE9&state=x"})

    def _token(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "grant_type=refresh_token" in body:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "via-login", "refresh_token": "r2", "expires_in": 3600})

    respx.post(_TOKEN_URL).mock(side_effect=_token)
    token = await beatport._get_token()
    assert token == "via-login"
    saved = json.loads(beatport._TOKEN_CACHE_FILE.read_text())
    assert saved["access_token"] == "via-login"


@respx.mock
async def test_full_login_sequence_shares_cookie_jar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_USERNAME", "user@example.com")
    monkeypatch.setenv("BEATPORT_PASSWORD", "secret")
    docs = respx.get(_DOCS_URL).respond(text=_DOCS_HTML)
    # A cookie set at login must ride along on the authorize GET — proves one client.
    login = respx.post(_LOGIN_URL).respond(200, headers={"Set-Cookie": "sessionid=beatport-sess; Path=/"})
    authorize = respx.get(_AUTHORIZE_URL).respond(302, headers={"Location": f"{_POST_MESSAGE}?code=AUTHCODE&state=st"})
    token = respx.post(_TOKEN_URL).respond(
        json={"access_token": "final-access", "refresh_token": "final-refresh", "expires_in": 3600}
    )
    result = await beatport._get_token()
    assert result == "final-access"
    assert docs.called and login.called and authorize.called and token.called
    # One cookie jar across login -> authorize.
    assert "sessionid=beatport-sess" in authorize.calls[0].request.headers.get("cookie", "")
    # authorize carried the scraped client_id + a S256 PKCE challenge.
    aq = authorize.calls[0].request.url.params
    assert aq["client_id"] == _CLIENT_ID
    assert aq["response_type"] == "code"
    assert aq["code_challenge_method"] == "S256"
    assert aq["code_challenge"]
    # exchange swapped the code + verifier for tokens.
    body = token.calls[0].request.content.decode()
    assert "grant_type=authorization_code" in body
    assert "code=AUTHCODE" in body
    assert "code_verifier=" in body
    assert f"client_id={_CLIENT_ID}" in body
    # tokens persisted for reuse next run.
    saved = json.loads(beatport._TOKEN_CACHE_FILE.read_text())
    assert saved["access_token"] == "final-access"
    assert saved["refresh_token"] == "final-refresh"


@respx.mock
async def test_token_cache_file_is_chmod_600(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_USERNAME", "u")
    monkeypatch.setenv("BEATPORT_PASSWORD", "p")
    respx.get(_DOCS_URL).respond(text=_DOCS_HTML)
    respx.post(_LOGIN_URL).respond(200)
    respx.get(_AUTHORIZE_URL).respond(302, headers={"Location": f"{_POST_MESSAGE}?code=C"})
    respx.post(_TOKEN_URL).respond(json={"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    await beatport._get_token()
    mode = stat.S_IMODE(beatport._TOKEN_CACHE_FILE.stat().st_mode)
    assert mode == 0o600


@respx.mock
async def test_missing_credentials_raises_no_credentials() -> None:
    with pytest.raises(SourceLookupError) as ei:
        await beatport._get_token()
    assert "no credentials" in str(ei.value)
    assert respx.calls.call_count == 0


async def test_partial_credentials_raise_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # username but no password → still "no credentials" (enricher skips on that substring).
    monkeypatch.setenv("BEATPORT_USERNAME", "u")
    with pytest.raises(SourceLookupError) as ei:
        await beatport._get_token()
    assert "no credentials" in str(ei.value)


@respx.mock
async def test_malformed_cache_file_is_ignored() -> None:
    beatport._TOKEN_CACHE_FILE.write_text("{ this is not valid json", encoding="utf-8")
    # No creds: a malformed cache must be treated as absent, not crash the parse.
    with pytest.raises(SourceLookupError) as ei:
        await beatport._get_token()
    assert "no credentials" in str(ei.value)


# ---------------------------------------------------------------------------
# client_id scraping (rotates — never hardcoded).
# ---------------------------------------------------------------------------


def test_client_id_regex_extracts_from_blob() -> None:
    assert beatport._match_client_id(_DOCS_HTML) == _CLIENT_ID
    # Real Beatport bundle form: uppercase key, colon, single quotes (the case that
    # broke the live smoke — a case-sensitive regex missed CLIENT_ID).
    assert beatport._match_client_id("t.exports={CLIENT_ID:'" + _CLIENT_ID + "'}") == _CLIENT_ID
    # unquoted key + '=' assignment form, single quotes
    assert beatport._match_client_id("var clientid = 'ZZZZZZZZZZZZZZZZZZZZ';") == "Z" * 20
    # too short (<20 chars) and absent → no match
    assert beatport._match_client_id('client_id="short"') is None
    assert beatport._match_client_id("nothing here") is None


@respx.mock
async def test_client_id_scrape_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_USERNAME", "u")
    monkeypatch.setenv("BEATPORT_PASSWORD", "p")
    respx.get(_DOCS_URL).respond(text="<html><body>no client id anywhere</body></html>")
    with pytest.raises(SourceLookupError) as ei:
        await beatport._get_token()
    assert "client_id" in str(ei.value)


# ---------------------------------------------------------------------------
# lookup_beatport search + extraction (auth mocked via a seeded token cache).
# ---------------------------------------------------------------------------

_BP_TRACKS_RESPONSE = {
    "results": [
        {
            "id": 777,
            "name": "Fall Down",
            "mix_name": "Calibre Remix",
            "artists": [{"name": "Roni Size"}],
            "remixers": [{"name": "Calibre"}],
            "release": {"name": "Fall Down EP", "label": {"name": "V Recordings"}},
            "publish_date": "2019-03-01",
            "length_ms": 372000,
            "genre": {"name": "Drum & Bass"},
            "isrc": "GBABC1900123",
        }
    ]
}


@respx.mock
async def test_beatport_extraction() -> None:
    _write_cache(access_token="static-token")
    route = respx.get(_TRACKS_URL).respond(json=_BP_TRACKS_RESPONSE)
    track = _track(name="Fall Down (Calibre Remix)", artist="Roni Size")
    cands = await lookup_beatport(track)
    assert route.calls[0].request.headers["Authorization"] == "Bearer static-token"
    c = cands[0]
    assert c.source == "beatport"
    assert c.label == "V Recordings"
    assert c.year == "2019"
    assert c.remixer == "Calibre"
    assert c.mix == "Calibre Remix"
    assert c.duration_seconds == 372


async def test_beatport_missing_credentials_skips_via_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    # No creds, no cache → lookup surfaces the "no credentials" SourceLookupError
    # that the enricher special-cases into skip-and-continue.
    with pytest.raises(SourceLookupError) as ei:
        await lookup_beatport(_track(name="X", artist="Y"))
    assert "no credentials" in str(ei.value)


@respx.mock
async def test_lookup_401_invalidates_cache() -> None:
    _write_cache(access_token="expiring")
    respx.get(_TRACKS_URL).respond(401)
    with pytest.raises(SourceLookupError) as ei:
        await lookup_beatport(_track(name="X", artist="Y"))
    assert "401" in str(ei.value)
    assert beatport._token_cache is None
    assert not beatport._TOKEN_CACHE_FILE.exists()


# ---------------------------------------------------------------------------
# Year rule: no remix designator → earliest release year wins (regression).
# Among Beatport candidates that score identically (same name/mix/artist — e.g.
# an original vs. a Beatport reissue), the earliest publish year must surface
# first so it wins the confidence tie downstream in score_all's stable sort.
# ---------------------------------------------------------------------------

_BP_TIE_RESPONSE = {
    "results": [
        {
            "id": 1,
            "name": "Keep On",
            "mix_name": "Original Mix",
            "artists": [{"name": "Denham Audio"}],
            "release": {"name": "Keep On (Reissue)", "label": {"name": "Club Glow"}},
            "publish_date": "2019-05-01",
            "length_ms": 300000,
        },
        {
            "id": 2,
            "name": "Keep On",
            "mix_name": "Original Mix",
            "artists": [{"name": "Denham Audio"}],
            "release": {"name": "Keep On EP", "label": {"name": "Club Glow"}},
            "publish_date": "1999-03-01",
            "length_ms": 300000,
        },
    ]
}


@respx.mock
async def test_earliest_publish_year_wins_confidence_ties() -> None:
    _write_cache(access_token="static-token")
    respx.get(_TRACKS_URL).respond(json=_BP_TIE_RESPONSE)
    track = _track(name="Keep On", artist="Denham Audio")
    cands = await lookup_beatport(track)
    assert cands[0].year == "1999"
    assert cands[0].source_id == "2"
