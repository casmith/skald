# Limitations

The honest list, so nothing here is a surprise.

## Getting events in

Two ways, and one of them has a caveat:

- **The log hook**, from the
  [`lloesche/valheim-server`](https://github.com/lloesche/valheim-server-docker)
  image: it writes only the lines Skald wants, so an update that rewords one
  shows up as a count on `/diagnostics` rather than as silence.
- **A server's own log file**, for vanilla and systemd installs. Works just
  as well, but Skald cannot tell a reworded line from the ordinary noise
  there, so it loses that early-warning signal.

Pointing it at a *container's* json log works but is fragile: the path holds
the container id, so recreating the container moves it.

## It cannot know what happened before it arrived

Sessions, deaths and exploration are reconstructed from log lines, so Skald
knows nothing about play from before the hook existed. You can backfill from
`docker logs` if the container still holds them (see
[installing](install.md)), but a recreated container has already lost them.

Boss kills are luckier: they are stored in the world, so Skald finds ones
that happened years ago. It just cannot say *when* — they show as "before"
the first save it saw, unless your backups reach back far enough to pin them.

## Boss kills are dated to the save interval

| Source | Window |
|---|---|
| A key new in an autosave | the save interval — 30 minutes by default |
| A key new in an hourly backup | about 90 minutes |
| The server logged the key being set | exact, but see below |

**Valheim does not log boss kills.** The game's code contains a
`Setting global key` message, and Skald reads it if it appears, but 1.0.x
does not print it: verified by killing Eikthyr on a test server and finding
no such line. There is no log level to turn it on either — the server has no
verbosity flag, and the message is compiled out of release builds.

So in practice a kill is dated to a window as wide as your save interval.
You can narrow it by saving more often:

```yaml
    environment:
      SERVER_ARGS: "-saveinterval 300"   # 5 minutes instead of 30
```

That is a real trade: every save writes the world out and players feel a
brief hitch, so it costs more on a large, busy world. `/diagnostics` shows
how often each world actually saves, which is the precision you are getting.

## Playtime is per character, not per account

Sessions are attributed to the character name the log reports. One person
playing two characters shows as two players; two people sharing a character
show as one.

## The world clock, and what hangs off it

Valheim's clock only advances while someone is online. Skald follows that:
the number comes from the last save, advanced by online time since. It is
accurate to a few minutes, and everything derived from it — the in-game day,
the weather, the forecast — is as accurate as that.

Weather itself is computed rather than observed, from the game's own tables.
Those were checked against Valheim 1.0.x; a future update could change them,
which is why `/diagnostics` shows the game version your servers report and
whether Skald has been verified against it.

## It shows who is playing, right now

That is the point, and it is also a log of when your friends are at their
computers. **Signing in does not gate any of it**: Skald has no access
control, so the dashboard is as public as wherever you host it. Keep it on
your own network, or put it behind something that authenticates, and think
before making it public.

The dashboard itself exposes no Steam IDs. If you turn on sign-in, Skald
stores the SteamID of anyone who signs in, plus their display name and
avatar if you gave it an API key.

## Deliberately not planned

- Anything that writes to your world, or needs a mod installed in the game.
- Anything that needs Valheim's own assets or art.
