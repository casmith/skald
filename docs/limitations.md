# Limitations

The honest list, so nothing here is a surprise.

## It only works with one server image, for now

Events reach Skald through a log hook that the
[`lloesche/valheim-server`](https://github.com/lloesche/valheim-server-docker)
image provides. A vanilla or systemd server writes the same lines to its own
log, and reading that directly is the next thing on the
[roadmap](../ROADMAP.md) — until then, Skald has no way in.

## It cannot know what happened before it arrived

Sessions, deaths and exploration are reconstructed from log lines, so Skald
knows nothing about play from before the hook existed. You can backfill from
`docker logs` if the container still holds them (see
[installing](install.md)), but a recreated container has already lost them.

Boss kills are luckier: they are stored in the world, so Skald finds ones
that happened years ago. It just cannot say *when* — they show as "before"
the first save it saw, unless your backups reach back far enough to pin them.

## Timing is as precise as the evidence allows

| Source | Window |
|---|---|
| The server logged the key being set | exact, to the second |
| A key new in an autosave | 30 minutes |
| A key new in an hourly backup | about 90 minutes |

Whether the server logs global keys at all depends on the game version, so
the exact case is a bonus, not a promise.

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
computers. There is no authentication in Skald. Keep it on your own network,
or put it behind something that authenticates, and think before making it
public. It exposes no Steam IDs.

## Deliberately not planned

- Anything that writes to your world, or needs a mod installed in the game.
- Anything that needs Valheim's own assets or art.
