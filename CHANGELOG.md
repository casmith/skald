# Changelog

## Unreleased

## 0.1.0 — first release worth sharing

The first version documented well enough for someone else to run, and tested
by doing exactly that on a clean machine.

- **The quick start works from nothing**, which it did not before: a fresh
  Docker volume is root-owned and Skald runs unprivileged, so it could not
  create its database and every page failed — while the container reported
  healthy, because `/healthz` only proved a socket was open. The image now
  ships its directories with the right ownership, `/healthz` answers only
  when the database is reachable, and Skald exits with the remedy rather
  than serving failures behind a green tick.
- **Docs**: [installing](docs/install.md), [configuration](docs/configuration.md),
  [how it works](docs/how-it-works.md), and an honest
  [limitations](docs/limitations.md) page.
- `compose.example.yaml` requires a server password instead of quietly
  handing the game servers a blank one.
- `tools/demo.py` builds a month of invented history, for trying Skald
  without a game server — and for screenshots, so no real player's name is
  ever needed in this repository.

## 0.0.2

- **Configuration** from a TOML file, with environment variables over it.
  Worlds can say where their saves and backups live. The older `TRACKER_*`
  names still work.
- **`/diagnostics`**: every path and world, what is arriving and what is
  not, and where each setting came from.
- **SQLite** under `data_dir`, with migrations. Every event line is ingested
  once and kept, read incrementally instead of re-reading whole files, so
  history outlives the log files. A `milestones.json` from 0.0.1 is imported
  on first run.
- **Runs as uid 10001** instead of root.
- **Notices when Valheim moves**: the game version each server reports, the
  save format's version, and a count of log lines matching nothing — all on
  `/diagnostics`, so a reworded line shows up instead of going quiet.

## 0.0.1

Extracted from the homelab deploy repository it grew up in, with the tests
it never had. Writing them found three bugs:

- a session whose `Closing socket` line never arrived was merged with the
  player's next one, counting the hours between as play;
- the forecast's footnote named biomes past the progress gate;
- a world whose bosses fell before Skald was watching showed no badges.
