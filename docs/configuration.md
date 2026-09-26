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
| `base_url` | `SKALD_BASE_URL` | — | The public address people reach Skald at. Setting it turns on sign-in |
| `steam_api_key` | `SKALD_STEAM_API_KEY` | — | Optional: a free Steam Web API key, for display names and avatars |
| `session_days` | `SKALD_SESSION_DAYS` | `30` | How long a sign-in lasts |

A world's `saves_dir` and `backups_dir` override the roots for that world
alone.

**The older `TRACKER_*` names still work** (`TRACKER_SERVERS`, `TRACKER_TZ`,
`TRACKER_PORT`, `TRACKER_DEFAULT_WORLD`, and the bare `EVENTS_DIR`,
`DATA_DIR`, `SAVES_ROOT`, `BACKUPS_ROOT`), because Skald grew out of a
deployment that sets them.

## Sign in through Steam

Optional, and off until `base_url` is set. Skald has to know the public
address to send people back to, and guessing it would be a way to send them
somewhere they never came from.

```toml
base_url = "https://skald.example.com"      # exactly as people reach it
steam_api_key = "…"                         # optional, for names and avatars
```

Behind a reverse proxy, `base_url` is the **outside** address, not the
container's.

**It is OpenID 2.0, not OAuth2** — Steam has never offered OAuth2 for
third-party sites. There is nothing to register, no client secret, and no
approval from Valve. What comes back is a SteamID64 and nothing else: no
email, no friends, no library, and no ability to act on anyone's behalf.

Skald stores the SteamID, and the display name and avatar if you configured
an API key. Sessions are a random token in a cookie (`HttpOnly`,
`SameSite=Lax`, and `Secure` when `base_url` is https); the database keeps
only a hash of it, so a stolen copy of the database cannot be used to sign
in as anyone.

Nothing about the dashboard changes for people who do not sign in.

### Your page

`/me` is the one page that is yours. It shows your playtime over the usual
windows, your deaths and deaths per 10 hours played, your longest session,
where you stand among everyone on the server, your hours and deaths per day,
and your last few sessions — all for your primary character, with a line for
anything you have played as your others.

It is the dashboard's own numbers narrowed to you, not a second calculation,
so the two always agree.

### Your characters

A Valheim player can have several characters, and a server log names the
character, not the account. Skald pairs the two itself: a connection logs a
SteamID, and the character that follows it on that connection is who that
account turned out to be. So `/me` lists the characters your Steam account
has actually been seen playing, and **the one you have played most is your
primary** — chosen automatically, no ceremony.

Change it on that page and it stays where you put it, however much you play
the others. You can also mark a character as not yours (a shared account, a
friend's machine), and take that back later.

There is nothing to type in, which is the point: on a public instance a
free-form claim would let anyone take any name. Here, taking a character
means having the Steam account that played it.

What you own is **private**. Characters and Steam IDs never appear on the
dashboard, in the API, or to anyone else — the only thing sign-in changes on
the public page is that *you* are greeted by your character's name.

## Where a setting came from

`/diagnostics` lists every setting with its source: the file, the exact
variable, or the default. It is the quickest way to find the override you
forgot.

## The pages and the API

| Path | What |
|---|---|
| `/` | The dashboard. `?world=Utgard` picks a world; tabs are plain links |
| `/me` | Your numbers, your characters and your last few sessions. Signed in only |
| `/diagnostics` | What Skald can see, and what it cannot |
| `/api/online` | Who is on each world now |
| `/api/playtime` | Hours and deaths per player, per window |
| `/api/sessions` | Recent sessions (`?limit=`) |
| `/api/deaths` | Recent deaths (`?limit=`) |
| `/api/daily` | Player-hours, deaths and landmarks per day (`?days=`) |
| `/api/world` | How each world is set up: modifiers, preset, game version |
| `/api/raids` | Raids, newest first, with who was online |
| `/api/milestones` | Boss kills and firsts, with how each was dated |
| `/api/weather` | Each world's clock, phase and per-biome weather |
| `/api/diagnostics` | The diagnostics page, as JSON |
| `/healthz` | 200 when the database is reachable, 503 when it is not |

Every JSON endpoint takes `?world=` to narrow it to one world.
