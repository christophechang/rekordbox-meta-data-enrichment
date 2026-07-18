from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx
from factories import _track

from enricher.beatport import lookup_beatport
from enricher.lookup import SourceLookupError

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


@pytest.fixture(autouse=True)
def _reset_bp_token_cache() -> Iterator[None]:
    # The token cache is a module-level global; clear it around every test so the
    # suite is order-independent and hermetic regardless of the developer's env.
    from enricher import beatport

    beatport._token_cache.clear()
    yield
    beatport._token_cache.clear()


@respx.mock
async def test_beatport_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEATPORT_API_TOKEN", "static-token")
    route = respx.get("https://api.beatport.com/v4/catalog/tracks/").respond(json=_BP_TRACKS_RESPONSE)
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


@respx.mock
async def test_beatport_client_credentials_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEATPORT_API_TOKEN", raising=False)
    monkeypatch.setenv("BEATPORT_CLIENT_ID", "cid")
    monkeypatch.setenv("BEATPORT_CLIENT_SECRET", "sec")
    respx.post("https://api.beatport.com/v4/auth/o/token/").respond(json={"access_token": "fresh"})
    respx.get("https://api.beatport.com/v4/catalog/tracks/").respond(json={"results": []})
    await lookup_beatport(_track(name="X", artist="Y"))  # must not raise


async def test_beatport_missing_credentials_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("BEATPORT_API_TOKEN", "BEATPORT_CLIENT_ID", "BEATPORT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SourceLookupError):
        await lookup_beatport(_track(name="X", artist="Y"))
