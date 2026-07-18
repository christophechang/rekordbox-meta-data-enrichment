from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx

from enricher import daemon

_WATCH_CONTENT = b"<?xml version='1.0'?><DJ_PLAYLISTS Version='1.0.0'></DJ_PLAYLISTS>"

# One entry per changed field (mirrors reporter.build_changes output). Two distinct
# tracks: track 1 gets label+year from beatport, track 2 gets a remixer from discogs.
_CHANGES: list[dict[str, object]] = [
    {
        "track_id": "1",
        "artist": "Denham Audio",
        "title": "Keep On",
        "field": "label",
        "old": "",
        "new": "Rekids",
        "source": "beatport",
        "confidence": 0.92,
        "colour": "0x00FF00",
    },
    {
        "track_id": "1",
        "artist": "Denham Audio",
        "title": "Keep On",
        "field": "year",
        "old": "",
        "new": "2022",
        "source": "beatport",
        "confidence": 0.92,
        "colour": "0x00FF00",
    },
    {
        "track_id": "2",
        "artist": "Roni Size",
        "title": "Fall Down",
        "field": "remixer",
        "old": "",
        "new": "Calibre",
        "source": "discogs",
        "confidence": 0.88,
        "colour": "0xFFA500",
    },
]


def _make_config(
    tmp_path: Path,
    *,
    token: str | None = None,
    channel: str | None = None,
    sources: str = "all",
) -> daemon._Config:
    out = tmp_path / "out"
    watch = tmp_path / "rekordbox.xml"
    watch.write_bytes(_WATCH_CONTENT)
    return daemon._Config(
        watch_file=watch,
        out_dir=out,
        state_file=out / ".daemon-state.json",
        cache_file=out / ".enrichment_cache.json",
        sources=sources,
        timeout_secs=7200,
        discord_token=token,
        discord_channel=channel,
    )


def _install_fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    changes: list[dict[str, object]] | None = None,
    stdout: str = "enricher stdout",
    stderr: str = "",
    record: dict[str, object] | None = None,
) -> None:
    """Replace subprocess.run with a fake that emulates the enricher writing its
    outputs (import XML + changes.json) under the --output directory."""

    def fake_run(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if record is not None:
            record["argv"] = argv
            record["kwargs"] = kwargs
        out_xml = Path(argv[argv.index("--output") + 1])
        if returncode == 0:
            out_xml.parent.mkdir(parents=True, exist_ok=True)
            out_xml.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            out_xml.with_suffix(".changes.json").write_text(
                json.dumps(changes if changes is not None else []), encoding="utf-8"
            )
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    # daemon references the same subprocess module object, so patching it here takes effect there.
    monkeypatch.setattr(subprocess, "run", fake_run)


# --- config -------------------------------------------------------------------


def test_load_config_requires_watch_file(tmp_path: Path) -> None:
    with pytest.raises(daemon._ConfigError):
        daemon._load_config({"ENRICH_OUT_DIR": str(tmp_path / "out")})


def test_load_config_requires_out_dir(tmp_path: Path) -> None:
    with pytest.raises(daemon._ConfigError):
        daemon._load_config({"ENRICH_WATCH_FILE": str(tmp_path / "w.xml")})


def test_load_config_defaults(tmp_path: Path) -> None:
    cfg = daemon._load_config({"ENRICH_WATCH_FILE": str(tmp_path / "w.xml"), "ENRICH_OUT_DIR": str(tmp_path / "out")})
    assert cfg.state_file == tmp_path / "out" / ".daemon-state.json"
    assert cfg.cache_file == tmp_path / "out" / ".enrichment_cache.json"
    assert cfg.sources == "all"
    assert cfg.timeout_secs == 7200
    assert cfg.discord_token is None
    assert cfg.discord_channel is None


def test_load_config_honours_overrides(tmp_path: Path) -> None:
    cfg = daemon._load_config(
        {
            "ENRICH_WATCH_FILE": str(tmp_path / "w.xml"),
            "ENRICH_OUT_DIR": str(tmp_path / "out"),
            "ENRICH_STATE_FILE": str(tmp_path / "custom-state.json"),
            "ENRICH_CACHE_FILE": str(tmp_path / "custom-cache.json"),
            "ENRICH_SOURCES": "beatport",
            "DISCORD_BOT_TOKEN": "tok",
            "ENRICH_DISCORD_CHANNEL_ID": "123",
        }
    )
    assert cfg.state_file == tmp_path / "custom-state.json"
    assert cfg.cache_file == tmp_path / "custom-cache.json"
    assert cfg.sources == "beatport"
    assert cfg.discord_token == "tok"
    assert cfg.discord_channel == "123"


def test_missing_required_config_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("ENRICH_WATCH_FILE", raising=False)
    monkeypatch.delenv("ENRICH_OUT_DIR", raising=False)
    assert daemon.main() == 2


# --- summary ------------------------------------------------------------------


def test_build_summary_counts_tracks_fields_and_sources() -> None:
    summary = daemon._build_summary(_CHANGES)
    assert "Enriched 2 tracks" in summary  # 2 distinct track ids
    assert "1 label" in summary
    assert "1 year" in summary
    assert "1 remixer" in summary
    assert "2 beatport" in summary
    assert "1 discogs" in summary
    assert "Updated Tracks" in summary


# --- hash guard ---------------------------------------------------------------


def test_hash_guard_skips_when_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    config.out_dir.mkdir(parents=True)
    input_hash = hashlib.sha256(_WATCH_CONTENT).hexdigest()
    config.state_file.write_text(json.dumps({"last_input_sha256": input_hash}), encoding="utf-8")

    called: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))

    assert daemon._run(config) == 0
    assert called == []  # enricher never invoked


def test_corrupt_state_is_treated_as_no_prior_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    config.out_dir.mkdir(parents=True)
    config.state_file.write_text("{ not valid json", encoding="utf-8")
    _install_fake_run(monkeypatch, changes=_CHANGES)
    assert daemon._run(config) == 0  # runs the enricher rather than crashing


# --- happy path ---------------------------------------------------------------


def test_happy_path_invokes_enricher_and_updates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    record: dict[str, object] = {}
    _install_fake_run(monkeypatch, changes=_CHANGES, record=record)

    assert daemon._run(config) == 0

    argv = record["argv"]
    assert isinstance(argv, list)
    assert argv[:3] == [sys.executable, "-m", "enricher"]
    assert argv[argv.index("--input") + 1] == str(config.watch_file)
    assert argv[argv.index("--sources") + 1] == "all"
    assert argv[argv.index("--output") + 1] == str(config.out_dir / "enrichment-import.xml")
    assert argv[argv.index("--report") + 1] == str(config.out_dir / "report.txt")
    assert argv[argv.index("--cache") + 1] == str(config.cache_file)

    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["last_input_sha256"] == hashlib.sha256(_WATCH_CONTENT).hexdigest()
    assert "last_run_utc" in state


def test_enrich_log_captures_stdout_and_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    _install_fake_run(monkeypatch, changes=_CHANGES, stdout="OUT-LINE", stderr="ERR-LINE")
    daemon._run(config)
    log = (config.out_dir / "enrich.log").read_text(encoding="utf-8")
    assert "OUT-LINE" in log
    assert "ERR-LINE" in log


def test_no_changes_updates_state_without_posting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, token="tok", channel="42")
    _install_fake_run(monkeypatch, changes=[])
    with respx.mock:
        assert daemon._run(config) == 0
        assert respx.calls.call_count == 0
    assert config.state_file.exists()


# --- read-only input ----------------------------------------------------------


def test_input_file_bytes_identical_before_and_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    _install_fake_run(monkeypatch, changes=_CHANGES)
    before = config.watch_file.read_bytes()
    daemon._run(config)
    assert config.watch_file.read_bytes() == before


# --- enricher failure ---------------------------------------------------------


def test_enricher_failure_does_not_update_state_and_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    _install_fake_run(monkeypatch, returncode=2, stderr="Traceback: boom")
    assert daemon._run(config) == 1
    assert not config.state_file.exists()


def test_failure_preserves_existing_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed run on a CHANGED input must leave a pre-existing state hash byte-for-byte intact.
    config = _make_config(tmp_path)
    config.out_dir.mkdir(parents=True)
    config.state_file.write_text(json.dumps({"last_input_sha256": "OLDHASH"}), encoding="utf-8")
    _install_fake_run(monkeypatch, returncode=1, stderr="boom")
    assert daemon._run(config) == 1
    assert json.loads(config.state_file.read_text())["last_input_sha256"] == "OLDHASH"


def test_run_passes_env_and_timeout_to_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Credential pass-through to the enricher depends on env=os.environ; the hang guard on timeout=.
    config = _make_config(tmp_path)
    record: dict[str, object] = {}
    _install_fake_run(monkeypatch, changes=_CHANGES, record=record)
    daemon._run(config)
    kwargs = record["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["env"] is os.environ
    assert kwargs["timeout"] == config.timeout_secs


def test_timeout_returns_1_and_preserves_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    config.out_dir.mkdir(parents=True)
    config.state_file.write_text(json.dumps({"last_input_sha256": "OLDHASH"}), encoding="utf-8")

    def _timeout_run(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _timeout_run)
    assert daemon._run(config) == 1  # a hang becomes a retryable failure, not an infinite block
    assert json.loads(config.state_file.read_text())["last_input_sha256"] == "OLDHASH"


def test_missing_watch_file_returns_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    config.watch_file.unlink()
    called = {"ran": False}

    def _should_not_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        called["ran"] = True
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(subprocess, "run", _should_not_run)
    assert daemon._run(config) == 2
    assert called["ran"] is False


@respx.mock
def test_enricher_failure_posts_text_alert_without_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, token="tok", channel="99")
    _install_fake_run(monkeypatch, returncode=1, stderr="boom")
    route = respx.post("https://discord.com/api/v10/channels/99/messages").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    assert daemon._run(config) == 1
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bot tok"
    assert b"enrichment-import.xml" not in request.content  # no attachment on failure alert
    assert not config.state_file.exists()


# --- discord delivery ---------------------------------------------------------


@respx.mock
def test_discord_post_includes_attachment_and_bot_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, token="secret-token", channel="12345")
    _install_fake_run(monkeypatch, changes=_CHANGES)
    route = respx.post("https://discord.com/api/v10/channels/12345/messages").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )

    assert daemon._run(config) == 0
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bot secret-token"
    body = request.content
    assert b"enrichment-import.xml" in body
    assert b"payload_json" in body


@respx.mock
def test_discord_unset_makes_no_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)  # no token/channel
    _install_fake_run(monkeypatch, changes=_CHANGES)
    assert daemon._run(config) == 0
    assert respx.calls.call_count == 0


@respx.mock
def test_discord_failure_does_not_fail_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, token="tok", channel="5")
    _install_fake_run(monkeypatch, changes=_CHANGES)
    respx.post("https://discord.com/api/v10/channels/5/messages").mock(
        return_value=httpx.Response(500, text="server error")
    )
    assert daemon._run(config) == 0  # delivery failure never fails the run
    assert config.state_file.exists()  # state still updated (file is on disk regardless)


# --- main never raises --------------------------------------------------------


def test_main_returns_1_on_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "load_dotenv", lambda *a, **k: None)

    def _boom(environ: object) -> daemon._Config:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(daemon, "_load_config", _boom)
    assert daemon.main() == 1  # exception is swallowed, never propagates
