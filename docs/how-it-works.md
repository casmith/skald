# How it works

Skald reads what a Valheim server already writes. No mod, no plugin, nothing
installed in the game, and nothing written back to your world.

## Names come from the log, because the query has none

Valheim answers a Steam query with a player *count* and blank names. So
names have to come from the server's own log, and a log hook copies the few
lines that matter into a file per world:

| Line | Means |
|---|---|
| `Got connection SteamID <id>` | someone connected |
| `Got character ZDOID from <name> : <a>:<b>` | ...and picked this character |
| `Got character ZDOID from <name> : 0:0` | that character **died** |
| `Closing socket <id>` | someone left |
| `Game - OnApplicationQuit` | the server shut down |
| `Placed location <X> in zone <x>,<y>` | a zone was generated with a landmark in it |
| `Setting global key <key>` | a world flag was set — a boss kill, usually |
| `Spawning boss at ...` | a boss was summoned at its altar |

Replaying those in order gives sessions, deaths and exploration. The awkward
cases are the ones worth knowing about:

- **A death looks like a join.** Valheim logs a death as the character
  "rejoining" with ZDOID `0:0`, and the respawn a few seconds later looks
  like another join. Neither starts a session.
- **A drop and a quick rejoin is one session.** Within two minutes, it is
  counted as continuous.
- **A missed goodbye is not.** If the `Closing socket` line never arrives —
  a crash, a kill -9 — the session is *not* merged with the player's next
  one, or the hours between would count as play.
- **A crash leaves a session open forever**, so Skald also polls each
  world's status endpoint. When the world reports nobody online while a
  session is still open, it closes that session at the last moment anyone
  was seen.

## Exploration

`Placed location` appears when the server generates a zone containing a
landmark — a crypt, a ruin, a camp. Zones with nothing in them are never
logged, so this measures the *pace* of exploration rather than every metre
walked. It matches the world's growth: on the deployment this was built for,
the count sat at zero for exactly the days the saved world stopped growing.

## Boss kills come from the world, not the log

A kill sets a permanent global key (`defeated_eikthyr`, `bosshildir1`,
`killedtroll`, …) that is saved with the world. Skald dates each one from
the best source it has:

1. **The log**, if the server recorded the key being set: exact, and with
   the preceding `Spawning boss` line, how long the fight took.
2. **The autosaves**, read every minute: a key new in one save was set
   between it and the last, and both times are known exactly — a 30-minute
   window.
3. **The hourly backups**: about 90 minutes, but they reach back as far as
   your retention, so they date kills from before Skald arrived.

A log time is only trusted when it falls inside the save or backup window
for that key, so a server that re-logs existing keys on restart cannot pass
one off as a fresh kill.

Bosses you have not beaten are never named on the page — the name is not in
the HTML at all — and biomes unlock as the boss before them falls.

## The world clock

A world save's header carries the world's own elapsed seconds. That clock
**only advances while someone is online**: the server stops it when a world
empties. (Checked against 312 hourly backups of a real world: the clock
matched online time in 257 of them and wall-clock time in 58, and never
moved while the world sat empty.) Skald takes the clock from the last save
and adds the online time since.

An earlier version used a number from the autosave log line that looked like
the world clock and was really the server's uptime. It reset on every
restart. This is why `/diagnostics` exists.

## The weather is computed

Valheim's weather is deterministic: every 666 seconds of *world* time the
game rolls one number, and each biome turns that roll into its own weather
through a weighted table. The world seed plays no part — every world sees
the same sequence, and only its clock decides where in it. Wind comes from
four octaves of the same generator.

So Skald computes the weather rather than observing it, from the clock
above. The day/night phase uses the game's own boundaries: a new day at
03:36, daylight 06:00–18:00, night from 20:24, nine minutes of the
thirty-minute day.

The engine is a port of [Jere Kuusela's
valheim-weather](https://github.com/JereKuusela/valheim-weather) (public
domain), and the tests check it against that implementation's own vectors.

## Where it all lives

Event files are where new lines arrive, not where they stay. Every line is
taken into SQLite once, keyed by its own text, so a backfill can overlap the
live file harmlessly and a rotated or trimmed file loses nothing. History
outlives the files: back up `data_dir/skald.db` and the event files are
replaceable.
