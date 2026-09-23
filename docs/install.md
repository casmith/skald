# Installing Skald

Skald is one container. It needs to see three things: the events the game's
log hook writes, each world's save directory, and (optionally) your backups.

## Requirements

- **Docker**, and game servers running the
  [`lloesche/valheim-server`](https://github.com/lloesche/valheim-server-docker)
  image. That image can run a command on log lines matching a pattern, which
  is how events reach Skald. Other server setups are not supported yet — see
  [limitations](limitations.md).
- Each game server needs `STATUS_HTTP: "true"`, which Skald polls to notice
  crashes and to read the game version.

## From nothing

```sh
curl -fsSLO https://raw.githubusercontent.com/casmith/skald/main/compose.example.yaml
mv compose.example.yaml compose.yaml
echo 'SERVER_PASS=choose-something' > .env
docker compose up -d
```

That brings up two Valheim servers and Skald, at `http://<host>:8080`.

## Adding Skald to servers you already run

Three changes to your compose file.

**1. Give every game server the log hook and a shared volume:**

```yaml
x-skald-hook: &skald-hook
  VALHEIM_LOG_FILTER_REGEXP_Skald: "Got connection SteamID|Got character ZDOID from|Closing socket|OnApplicationQuit|Placed location |Setting global key |Spawning boss "
  ON_VALHEIM_LOG_FILTER_REGEXP_Skald: 'cat >> "/events/$${WORLD_NAME}.log"'

services:
  midgard:
    environment:
      <<: *skald-hook
      STATUS_HTTP: "true"
    volumes:
      - skald_events:/events
```

The hook runs asynchronously and the matched lines still reach `docker logs`
exactly as before. `$$` is a literal `$` for the hook's shell.

**2. Add Skald**, with the events volume, its own data volume, and each
world's config volume mounted read-only at `/saves/<WORLD_NAME>`:

```yaml
  skald:
    image: ghcr.io/casmith/skald:0.1.0
    ports: ["8080:8080"]
    volumes:
      - skald_events:/events
      - skald_data:/data
      - midgard_config:/saves/Midgard:ro
    environment:
      SKALD_WORLDS: "Midgard=http://midgard/status.json"
      SKALD_TZ: America/Chicago
```

**3. Recreate the game servers** so they pick up the hook. That disconnects
whoever is playing, and it discards each container's `docker logs` — which
is the only record of play from before the hook existed. If you want that
history, keep it first:

```sh
docker logs midgard 2>&1 | grep -E 'Got connection SteamID|Got character ZDOID from|Closing socket|OnApplicationQuit|Placed location |Setting global key |Spawning boss ' > midgard.backfill.log
# after the deploy
docker exec -i skald sh -c 'cat > /events/Midgard.backfill.log' < midgard.backfill.log
```

Skald deduplicates on the line itself, so an overlap with the live file is
harmless.

## What to expect at first

A brand-new world looks empty, and should. Sessions and deaths appear as
people play; the in-game clock, weather and forecast need the world's first
autosave, which Valheim writes every 30 minutes. Boss milestones appear as
soon as a save (or a backup) shows their flag. `/diagnostics` says which of
those Skald is still waiting for.

## Upgrading

Pull the new tag and recreate the container. Skald keeps everything in
`data_dir/skald.db`, migrates it on start, and imports a `milestones.json`
from before 0.0.2 once. Back that database up and you have every session,
death and milestone — the event files are replaceable.

## Permissions

Skald runs as uid 10001.

- **Named Docker volumes need nothing.** The image ships `/data` and
  `/events` with the right ownership, and Docker copies that onto a fresh
  volume.
- **Bind mounts, and volumes created before 0.1.0, need a hand**, because
  they keep the host's ownership:

  ```sh
  docker run --rm -v <data volume>:/d alpine chown -R 10001:10001 /d
  chmod 1777 /path/to/events   # shared with the game's log hook
  ```

`/events` is world-writable on purpose: the hook writes there as whichever
user the game container runs as, which is not the same user as Skald.

If Skald cannot open its database it exits and says so, naming the directory
and the fix, rather than serving pages that fail. `/healthz` answers only
when the database is reachable, so a healthy container is a working one.
