# Changelog

## Unreleased

## 0.4.1

- **Wind direction was backwards.** Skald showed the direction the wind came
  *from* — the meteorological convention, right for a forecast and exactly
  opposite to what a player standing in it sees. Valheim's own bearing, the
  one a ship's wind indicator points along, is the direction it blows
  *toward*. The arrow and the compass letter now match the game. Reported
  from a live server, where the page said NE and the wind was blowing SW.
- The weather engine was never wrong: the angle always matched the reference
  implementation's vectors. Only the page turned it around. The regression
  test now pins the displayed bearing to that angle, so the two cannot part
  again.
- **API change:** `wind_from` is now `wind_dir` in `/api/weather`, because
  the old name described the old, wrong reading.

## 0.4.0

- **Your own page.** `/me` grew from a list of characters into your numbers:
  hours over 24 hours, 7 days, 30 days and all time, deaths and deaths per
  10 hours played, your longest session, when you were first seen, where you
  stand among everyone on the server, your hours and deaths per day, and
  your last few sessions.
- It is the dashboard's own `playtime`, `daily` and `recent` narrowed to
  your characters, not a second set of arithmetic — so there is one
  definition of an hour played and your page cannot drift from the table you
  appear in.
- Your other characters get a line rather than being folded into the
  headline, since the numbers people mean are their primary's.
- Still nothing new on the public page, and still no Steam IDs anywhere.

## 0.3.0

- **Your characters, worked out rather than claimed.** A Valheim log names
  characters, not accounts — but a connection logs a SteamID and the
  character that follows it is whoever that connection turned out to be, so
  Skald knows the pairing already. `/me` lists the characters your Steam
  account has been seen playing, and **your most-played one is your primary
  automatically**. Pick a different one and it stays put; mark one as not
  yours and it stops being offered.
- There is nothing to type in, deliberately: on a public instance a
  free-form claim would let anyone take any name. Taking a character here
  means having the Steam account that played it.
- Your characters are **private** — never on the dashboard, never in the
  API, and no Steam ID ever is. The only public effect of signing in is that
  the page greets you by your character's name instead of your Steam
  persona.
- `tools/demo.py` now gives a player a second character and renders the page,
  so the screenshot is made the same way every other one is.

## 0.2.0

- **Skald no longer needs the lloesche image.** A world can name its own
  `log_file`, and Skald reads the server's whole log — skipping the noise,
  reading incrementally, and unwrapping a container's json log format if it
  finds one. Vanilla and systemd servers work now.
- A bind mount whose source has gone leaves an empty *directory* behind,
  which Skald counted as an existing but empty log. It now checks for a
  file, and says so on `/diagnostics`.
- Ingesting outside the page's own path left the replay cache stale.
  Ingestion invalidates it itself, so call order cannot matter.
- **Optional sign in through Steam**, off until `base_url` is set. It is
  OpenID 2.0 — Steam offers no OAuth2 to third-party sites — so there is
  nothing to register and no client secret, and what comes back is a
  SteamID64 and nothing else. A display name and avatar need a free Steam
  Web API key, which is optional. Sessions are a random token in an
  HttpOnly, SameSite=Lax cookie (Secure over https); the database keeps only
  its hash. It gates nothing yet: the dashboard stays exactly as public as
  wherever you host it.

## 0.1.1

- **Boss kills are dated to the save interval, and the docs now say so
  plainly.** Valheim does not log a kill: the game's `Setting global key`
  message is compiled out of release builds, and the server has no verbosity
  flag to bring it back. Verified by killing Eikthyr on a test server and
  finding no such line. Skald still reads it if it ever appears.
- `/diagnostics` shows **how often each world actually saves**, measured
  from the last two saves, because that is the precision every boss kill is
  dated to. Tighten it with `SERVER_ARGS: "-saveinterval 300"`, which is now
  documented and tested (5-minute saves confirmed on a live server).

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
