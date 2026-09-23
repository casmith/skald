# Configuration

Settings come from a TOML file, with environment variables overriding it,
and defaults under both. Both are optional.

## The file

Mount it at `/config/skald.toml`, or point `SKALD_CONFIG` somewhere else.
There is a complete example in
[`skald.example.toml`](../skald.example.toml).

```toml
timezone = "America/Chicago"
default_world = "Midgard"

[[worlds]]
name = "Midgard"                 # must match the game's WORLD_NAME
status_url = "http://midgard/status.json"

[[worlds]]
name = "Utgard"
status_url = "http://utgard/status.json"
saves_dir = "/mnt/other/Utgard/worlds_local/Utgard"   # when it isn't under saves_root
backups_dir = "/mnt/backups/utgard"
```

## Every setting

| Setting | Variable | Default | What it does |
|---|---|---|---|
| `worlds` | `SKALD_WORLDS` | — | `Name=http://host/status.json`, comma separated, if you would rather not write the file |
| `default_world` | `SKALD_DEFAULT_WORLD` | first world | Which tab opens first. A name that matches no world is ignored, and `/diagnostics` says so |
| `port` | `SKALD_PORT` | `8080` | Port to listen on |
| `timezone` | `SKALD_TZ` | `UTC` | Dates, and where a day starts for the charts. Any zone name, e.g. `Europe/Stockholm` |
| `events_dir` | `SKALD_EVENTS_DIR` | `/events` | Where the log hook writes |
| `data_dir` | `SKALD_DATA_DIR` | `/data` | Skald's database |
| `saves_root` | `SKALD_SAVES_ROOT` | `/saves` | `<root>/<World>/worlds_local/<World>/` |
| `backups_root` | `SKALD_BACKUPS_ROOT` | `/nas` | `<root>/<world lowercased>/backups/worlds-*.zip`. Optional |
| `poll_seconds` | `SKALD_POLL_SECONDS` | `60` | How often each world's status endpoint is checked |
| `save_scan_seconds` | `SKALD_SAVE_SCAN_SECONDS` | `60` | How often saves are read; backups every tenth scan |
| `chart_days` | `SKALD_CHART_DAYS` | `30` | Days in the charts and the daily table |
| `merge_gap_seconds` | `SKALD_MERGE_GAP_SECONDS` | `120` | A rejoin this soon after leaving continues the same session |

A world's `saves_dir` and `backups_dir` override the roots for that world
alone.

**The older `TRACKER_*` names still work** (`TRACKER_SERVERS`, `TRACKER_TZ`,
`TRACKER_PORT`, `TRACKER_DEFAULT_WORLD`, and the bare `EVENTS_DIR`,
`DATA_DIR`, `SAVES_ROOT`, `BACKUPS_ROOT`), because Skald grew out of a
deployment that sets them.

## Where a setting came from

`/diagnostics` lists every setting with its source: the file, the exact
variable, or the default. It is the quickest way to find the override you
forgot.

## The pages and the API

| Path | What |
|---|---|
| `/` | The dashboard. `?world=Utgard` picks a world; tabs are plain links |
| `/diagnostics` | What Skald can see, and what it cannot |
| `/api/online` | Who is on each world now |
| `/api/playtime` | Hours and deaths per player, per window |
| `/api/sessions` | Recent sessions (`?limit=`) |
| `/api/deaths` | Recent deaths (`?limit=`) |
| `/api/daily` | Player-hours, deaths and landmarks per day (`?days=`) |
| `/api/milestones` | Boss kills and firsts, with how each was dated |
| `/api/weather` | Each world's clock, phase and per-biome weather |
| `/api/diagnostics` | The diagnostics page, as JSON |
| `/healthz` | 200 when the database is reachable, 503 when it is not |

Every JSON endpoint takes `?world=` to narrow it to one world.
