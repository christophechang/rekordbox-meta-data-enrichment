"""Enrich-on-export daemon: a launchd-triggered, single-shot wrapper around the
``enricher`` CLI.

launchd ``WatchPaths`` invokes this program once per change to the watched
Rekordbox XML export. Each invocation:

1. sha256-guards the input so re-triggers (and the daemon's own writes) are
   idempotent — an unchanged input is skipped;
2. shells out to ``python -m enricher`` to produce the import XML + changes.json;
3. summarises the changes and posts the summary plus the import XML to Discord.

The daemon never re-implements enrichment, never modifies its input, and writes
only inside ``ENRICH_OUT_DIR``. ``main()`` never raises — it always returns an
exit code — and secrets are never logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

_DISCORD_API = "https://discord.com/api/v10"
_IMPORT_XML_NAME = "enrichment-import.xml"
_REPORT_NAME = "report.txt"
_ENRICH_LOG_NAME = "enrich.log"
_CHANGES_SUFFIX = ".changes.json"
_STDERR_TAIL_CHARS = 2000
_HTTP_TIMEOUT = 30.0
# File-stability wait: launchd's WatchPaths fires the instant the watched file starts
# changing, so a non-atomic export copy can be read mid-write (a truncated, unparseable
# XML). Wait until (size, mtime) hold steady across a few checks before processing.
_STABLE_CHECKS = 3
_STABLE_INTERVAL_S = 1.5
_STABLE_TIMEOUT_S = 90.0
# Generous ceiling: a cold full-library run can take ~90 min; this only fires on a
# genuine hang. launchd runs one instance at a time, so an unbounded hang would
# silently stall every future trigger — the timeout turns that into a retryable failure.
_DEFAULT_TIMEOUT_SECS = 7200


class _ConfigError(Exception):
    """Raised when required daemon configuration is missing."""


@dataclass(frozen=True)
class _Config:
    watch_file: Path
    out_dir: Path
    state_file: Path
    cache_file: Path
    sources: str
    timeout_secs: int
    discord_token: str | None
    discord_channel: str | None

    @property
    def import_xml(self) -> Path:
        return self.out_dir / _IMPORT_XML_NAME

    @property
    def changes_file(self) -> Path:
        return self.out_dir / f"{Path(_IMPORT_XML_NAME).stem}{_CHANGES_SUFFIX}"


def _log(message: str) -> None:
    """Emit an operational log line to stderr (captured by launchd). Never contains secrets."""
    print(f"[enricher-daemon] {message}", file=sys.stderr, flush=True)


def _load_config(environ: Mapping[str, str]) -> _Config:
    watch_raw = environ.get("ENRICH_WATCH_FILE")
    out_raw = environ.get("ENRICH_OUT_DIR")
    missing = [name for name, value in (("ENRICH_WATCH_FILE", watch_raw), ("ENRICH_OUT_DIR", out_raw)) if not value]
    if missing:
        raise _ConfigError(f"missing required env var(s): {', '.join(missing)}")
    assert watch_raw is not None and out_raw is not None  # narrowing for mypy

    watch_file = Path(watch_raw).expanduser()
    out_dir = Path(out_raw).expanduser()

    state_raw = environ.get("ENRICH_STATE_FILE")
    cache_raw = environ.get("ENRICH_CACHE_FILE")
    timeout_raw = environ.get("ENRICH_TIMEOUT_SECS")
    try:
        timeout_secs = int(timeout_raw) if timeout_raw else _DEFAULT_TIMEOUT_SECS
    except ValueError:
        timeout_secs = _DEFAULT_TIMEOUT_SECS
    if timeout_secs <= 0:
        timeout_secs = _DEFAULT_TIMEOUT_SECS
    return _Config(
        watch_file=watch_file,
        out_dir=out_dir,
        state_file=Path(state_raw).expanduser() if state_raw else out_dir / ".daemon-state.json",
        cache_file=Path(cache_raw).expanduser() if cache_raw else out_dir / ".enrichment_cache.json",
        sources=environ.get("ENRICH_SOURCES") or "all",
        timeout_secs=timeout_secs,
        discord_token=environ.get("DISCORD_BOT_TOKEN") or None,
        discord_channel=environ.get("ENRICH_DISCORD_CHANNEL_ID") or None,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_state_hash(state_file: Path) -> str | None:
    """Return the last-processed input sha256, or None if unavailable/corrupt."""
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, dict):
        value = raw.get("last_input_sha256")
        if isinstance(value, str):
            return value
    return None


def _write_state(state_file: Path, input_hash: str, now: datetime) -> None:
    """Atomically persist the processed input hash + run timestamp (tmp file + os.replace)."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_input_sha256": input_hash, "last_run_utc": now.isoformat()}
    tmp = state_file.with_name(f"{state_file.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, state_file)


def _load_changes(changes_file: Path) -> list[dict[str, object]]:
    """Read the enricher's changes.json. Missing/corrupt → empty list (never raises)."""
    try:
        raw = json.loads(changes_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _pluralise(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _build_summary(changes: list[dict[str, object]]) -> str:
    track_ids = {str(entry.get("track_id")) for entry in changes}
    field_counts: Counter[str] = Counter(str(entry.get("field")) for entry in changes)
    source_counts: Counter[str] = Counter(str(entry.get("source")) for entry in changes)

    fields_part = ", ".join(_pluralise(count, field) for field, count in sorted(field_counts.items()))
    sources_part = " / ".join(f"{count} {source}" for source, count in sorted(source_counts.items()))
    return (
        f"Enriched {len(track_ids)} tracks — {fields_part} ({sources_part}). "
        f'Import the "Updated Tracks" playlist in Rekordbox.'
    )


def _run_enricher(config: _Config) -> subprocess.CompletedProcess[str]:
    argv = [
        sys.executable,
        "-m",
        "enricher",
        "--input",
        str(config.watch_file),
        "--sources",
        config.sources,
        "--output",
        str(config.import_xml),
        "--report",
        str(config.out_dir / _REPORT_NAME),
        "--cache",
        str(config.cache_file),
    ]
    _log(f"running enricher: {' '.join(argv)}")
    return subprocess.run(
        argv, capture_output=True, text=True, env=os.environ, check=False, timeout=config.timeout_secs
    )


def _post_discord(*, channel_id: str, token: str, content: str, attachment: Path | None) -> None:
    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    if attachment is not None:
        data = {"payload_json": json.dumps({"content": content})}
        files = {"files[0]": (attachment.name, attachment.read_bytes(), "application/xml")}
        response = httpx.post(url, headers=headers, data=data, files=files, timeout=_HTTP_TIMEOUT)
    else:
        response = httpx.post(url, headers=headers, json={"content": content}, timeout=_HTTP_TIMEOUT)
    response.raise_for_status()


def _try_post_discord(config: _Config, content: str, attachment: Path | None) -> None:
    """Best-effort Discord delivery. Never raises — a delivery failure must not fail the run."""
    if not (config.discord_token and config.discord_channel):
        return
    try:
        _post_discord(
            channel_id=config.discord_channel,
            token=config.discord_token,
            content=content,
            attachment=attachment,
        )
        _log("posted summary to Discord")
    except Exception as exc:  # noqa: BLE001 — delivery is best-effort; log type only (no secrets)
        _log(f"Discord delivery failed (ignored): {type(exc).__name__}")


def _wait_until_stable(path: Path) -> bool:
    """Return True once (size, mtime) hold steady across consecutive checks — the copy
    has finished. Return False if it never settles within the timeout. Guards against
    reading a file mid-write when the watcher does a non-atomic copy."""
    last: tuple[int, float] | None = None
    stable = 0
    deadline = time.monotonic() + _STABLE_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            st = path.stat()
        except OSError:
            return False
        sig = (st.st_size, st.st_mtime)
        if sig == last:
            stable += 1
            if stable >= _STABLE_CHECKS:
                return True
        else:
            stable = 0
            last = sig
        time.sleep(_STABLE_INTERVAL_S)
    return False


def _is_valid_xml(path: Path) -> bool:
    """Cheap well-formedness check so a partial/malformed export is skipped quietly
    rather than crashing the enricher with a traceback."""
    from lxml import etree

    try:
        etree.parse(str(path))
        return True
    except (etree.XMLSyntaxError, OSError):
        return False


def _run(config: _Config) -> int:
    if not config.watch_file.is_file():
        _log(f"watch file not found: {config.watch_file}")
        return 2

    if not _wait_until_stable(config.watch_file):
        _log("watch file still changing after timeout — skipping, will retry on next trigger")
        return 0

    if not _is_valid_xml(config.watch_file):
        # Almost always a mid-copy partial that lost the race even after the stability
        # wait; skip quietly (no failure alarm) and let the next trigger reprocess.
        _log("watch file is not valid XML (partial copy or malformed) — skipping, will retry")
        return 0

    input_hash = _sha256_file(config.watch_file)
    if _load_state_hash(config.state_file) == input_hash:
        _log("unchanged since last run, skipping")
        return 0

    config.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = _run_enricher(config)
    except subprocess.TimeoutExpired:
        _log(f"enricher timed out after {config.timeout_secs}s; state NOT updated (will retry on next trigger)")
        _try_post_discord(config, f"Enrichment run timed out after {config.timeout_secs}s.", None)
        return 1
    (config.out_dir / _ENRICH_LOG_NAME).write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")

    if result.returncode != 0:
        _log(f"enricher exited {result.returncode}; state NOT updated (will retry on next trigger)")
        tail = (result.stderr or "")[-_STDERR_TAIL_CHARS:]
        if tail:
            _log(f"enricher stderr tail: {tail}")
        _try_post_discord(config, f"Enrichment run failed (exit {result.returncode}). See {_ENRICH_LOG_NAME}.", None)
        return 1

    changes = _load_changes(config.changes_file)
    if changes:
        summary = _build_summary(changes)
        _log(summary)
        _try_post_discord(config, summary, config.import_xml)
    else:
        _log("no blanks to fill — nothing to post")

    _write_state(config.state_file, input_hash, datetime.now(timezone.utc))
    return 0


def main() -> int:
    try:
        load_dotenv()
        config = _load_config(os.environ)
    except _ConfigError as exc:
        _log(f"configuration error: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 — main() must never raise
        _log(f"unexpected startup error: {type(exc).__name__}: {exc}")
        return 1

    try:
        return _run(config)
    except Exception as exc:  # noqa: BLE001 — main() must never raise
        _log(f"unexpected error: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
