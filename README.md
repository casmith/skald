# Skald

A player and world dashboard for Valheim servers: who is online, how long
everyone has played, how often they have died, when the bosses fell, and what
the weather is doing.

It reads what the server already writes — its log and its world saves — and
serves a single page. No mods, no plugins, nothing installed in the game, and
nothing written back to your world.

> **Status: early.** Working and in daily use on one homelab, but not yet
> released. See [ROADMAP.md](ROADMAP.md) for what v0.1 needs.

## What it shows

- **Who is online**, per world, and for how long.
- **Playtime and deaths** per player over 24 hours, 7 days, 30 days and all
  time, with deaths per 10 hours played.
- **Boss kills as achievement badges**, dated as precisely as the evidence
  allows. Bosses you have not beaten stay nameless, so the page never spoils
  what is ahead.
- **In-game weather** for each biome you have reached, the day/night phase,
  and a 7-day forecast.
- **Charts** of player-hours, deaths and exploration per day.
- JSON for all of it, if you would rather build your own view.

## How it knows

Valheim's Steam query reports a *player count* but blanks every name, so
names have to come from the server log:

| What | Where it comes from |
|---|---|
| Sessions, deaths | `Got connection SteamID` / `Got character ZDOID from` / `Closing socket` lines. A death is the character "rejoining" as `0:0`. |
| Exploration | `Placed location` lines: the server logs a zone the first time it generates one with a landmark in it. |
| Boss kills | Global keys (`defeated_eikthyr`, …) saved with the world. Dated from the log if the server logged the key being set, otherwise from consecutive autosaves, otherwise from hourly backups. |
| Weather | Computed. Valheim rolls one number per 666 seconds of *world* time and each biome reads it through a weighted table, so a world's clock decides its weather entirely. |
| The world clock | The double in a save's header, advanced by **online** time — the server stops the clock when a world empties. |

A crash leaves a session with no `Closing socket` line, so Skald also polls
each world's status endpoint and closes sessions it can prove ended.

## Requirements

**Today Skald only supports servers running the
[`lloesche/valheim-server`](https://github.com/lloesche/valheim-server-docker)
image**, which can run a hook on matching log lines. That hook is how events
reach Skald. Reading a plain log file (for vanilla or systemd installs) and
reading container logs are on the roadmap, and are what most people will
need — see [ROADMAP.md](ROADMAP.md).

It also needs read access to each world's save directory, and optionally to
your backups, which let it date kills that happened before Skald was watching.

## Quick start

See [`compose.example.yaml`](compose.example.yaml) for a complete file. In
short: add the log hook to each game server, mount the shared events volume
and the saves, and run Skald beside them.

```yaml
x-skald-hook: &skald-hook
  VALHEIM_LOG_FILTER_REGEXP_Skald: "Got connection SteamID|Got character ZDOID from|Closing socket|OnApplicationQuit|Placed location |Setting global key |Spawning boss "
  ON_VALHEIM_LOG_FILTER_REGEXP_Skald: 'cat >> "/events/$${WORLD_NAME}.log"'
```

Then open `http://<host>:8080`.

## Configuration

| Variable | Default | What |
|---|---|---|
| `TRACKER_SERVERS` | — | `World=http://host/status.json`, comma separated. The world name must match the game's `WORLD_NAME`. |
| `TRACKER_DEFAULT_WORLD` | first listed | Which world's tab opens first. |
| `TRACKER_PORT` | `8080` | Port to listen on. |
| `TRACKER_TZ` | `America/Chicago` | Timezone for dates and daily buckets. |
| `EVENTS_DIR` | `/events` | Where the hook writes its event files. |
| `DATA_DIR` | `/data` | Skald's own state. |
| `SAVES_ROOT` | `/saves` | `<SAVES_ROOT>/<World>/worlds_local/<World>/`. |
| `BACKUPS_ROOT` | `/nas` | `<BACKUPS_ROOT>/<world lowercased>/backups/worlds-*.zip`. Optional. |

## Privacy

Skald shows player names and who is playing right now. Think before putting
it on the public internet: it is a log of when your friends are at their
computers. It is read-only and exposes no Steam IDs, but the sensible default
is to keep it on your own network or behind an authenticating proxy.

## Attribution

The weather engine is a port of
[Jere Kuusela's valheim-weather](https://github.com/JereKuusela/valheim-weather)
(Unlicense, public domain), which is the reference for the period lengths, the
biome tables and Unity's random-number quirks. The rune-style glyphs are
original SVG.

Skald is a fan project, not affiliated with or endorsed by Iron Gate AB.
Valheim is their trademark.

MIT licensed — see [LICENSE](LICENSE).
