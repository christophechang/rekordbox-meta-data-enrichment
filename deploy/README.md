# Enrich-on-export daemon — Mini setup

A `launchd` agent that runs the enricher each time the watched Rekordbox XML
export changes, then posts the import delta to Discord. Single-shot: launchd
(`WatchPaths`) invokes it per file-change — there is no long-running loop.

## What it does per trigger

1. sha256-guards the input (skips if unchanged since the last run — makes
   re-triggers idempotent).
2. Runs `python -m enricher` to produce `enrichment-import.xml` +
   `enrichment-import.changes.json` under the output dir.
3. Summarises the changes and posts the summary + import XML to Discord.

It is read-only on the watched file and writes **only** inside `ENRICH_OUT_DIR`.

## 1. Clone + install

```sh
git clone <repo-url> ~/rekordbox-enricher
cd ~/rekordbox-enricher
python3.13 -m venv .venv
.venv/bin/pip install -e .
```

## 2. Create `.env` (in the repo root)

Secrets and config live here, **not** in the plist. `load_dotenv()` reads this
from the daemon's `WorkingDirectory`.

```sh
# --- daemon config ---
ENRICH_WATCH_FILE=/Users/<you>/mixlab/import/rekordbox.xml
ENRICH_OUT_DIR=/Users/<you>/mixlab/enricher-out
# ENRICH_SOURCES=all                # optional (default: all)
# ENRICH_STATE_FILE=...             # optional (default: <OUT_DIR>/.daemon-state.json)
# ENRICH_CACHE_FILE=...             # optional (default: <OUT_DIR>/.enrichment_cache.json)

# --- Discord (optional; both required to post) ---
DISCORD_BOT_TOKEN=...
ENRICH_DISCORD_CHANNEL_ID=...

# --- enricher credentials (passed through to the subprocess) ---
BEATPORT_USERNAME=...
BEATPORT_PASSWORD=...
DISCOGS_TOKEN=...
# LLM disambiguation keys (optional): MISTRAL_API_KEY / GROQ_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY
```

Create the output dir: `mkdir -p /Users/<you>/mixlab/enricher-out`.

## 3. Fill in the plist placeholders

`deploy/com.openclaw.rekordbox-enricher.plist` ships with `__PLACEHOLDER__`
tokens. Replace them and install to `~/Library/LaunchAgents/`:

```sh
REPO="$HOME/rekordbox-enricher"
WATCH="/Users/<you>/mixlab/import/rekordbox.xml"
OUT="/Users/<you>/mixlab/enricher-out"

sed -e "s#__REPO__#$REPO#g" \
    -e "s#__WATCH_FILE__#$WATCH#g" \
    -e "s#__OUT_DIR__#$OUT#g" \
    "$REPO/deploy/com.openclaw.rekordbox-enricher.plist" \
    > ~/Library/LaunchAgents/com.openclaw.rekordbox-enricher.plist
```

`ENRICH_WATCH_FILE` / `ENRICH_OUT_DIR` are set in the plist's
`EnvironmentVariables`; everything else comes from `.env`.

## 4. Load the agent

```sh
launchctl load ~/Library/LaunchAgents/com.openclaw.rekordbox-enricher.plist
# modern equivalent: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openclaw.rekordbox-enricher.plist
```

## 5. Test

launchd fires when the watched file changes. Stage an export, then:

```sh
touch "$WATCH"
```

Check `"$OUT"/launchd.log` (daemon stderr) and `"$OUT"/enrich.log` (enricher
stdout+stderr). On a changed input you should see the enricher run, a summary
line, and — if Discord is configured — a message with the import XML attached.

Re-running `touch` without changing the file contents is a no-op (the sha256
guard skips it and logs `unchanged since last run, skipping`).

## Updating / removing

```sh
# after pulling new code or editing the plist:
launchctl unload ~/Library/LaunchAgents/com.openclaw.rekordbox-enricher.plist
launchctl load   ~/Library/LaunchAgents/com.openclaw.rekordbox-enricher.plist
```

## Exit codes

- `0` — success (enriched, or skipped as unchanged, or no blanks to fill).
- `1` — enricher subprocess failed (state not updated → retries next trigger),
  or an unexpected error.
- `2` — missing required config (`ENRICH_WATCH_FILE` / `ENRICH_OUT_DIR`) or the
  watch file is absent.
