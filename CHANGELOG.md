# Changelog

## 0.7.1

- **A line is no longer counted twice because something quoted it.** The log
  hook announces its work by quoting the whole line it is about to write, so
  a full server log holds every hooked line twice -- and the copy differs
  from the original by one trailing quote, just enough to slip past the key
  that would have caught it. A timestamp inside quotes is now read as a line
  being quoted rather than a line being logged.
- Deaths were the casualty: they drive the per-player table, the
  deaths-per-hour figure and a chart. On the deployment that found this, 316
  of 2,834 events were copies and not one was a death -- that is luck, not
  design. Arrivals, departures and landmarks were never affected.
- Copies already stored are removed once on upgrade, and only those with a
  genuine twin. An unexplained row is not a reason to delete anything.

## 0.7.0

- **The group's map, with nobody uploading anything.** A world's own save
  holds its cartography table: everything anyone who used one has shared to
  it. Skald already mounts those saves to read the world clock, so `/map`
  now shows a real group map for every world it watches, kept current by the
  game itself.
- It is a far better source than an uploaded character. On the world this
  was built against, the table holds **154,355** explored pixels; the
  fullest single character file we had held 3,920. Nothing is uploaded,
  nothing leaves the server, and it covers everyone who used a table rather
  than everyone who could be bothered.
- Found by shape, like a character's map — a run of `edge × edge` bytes
  every one of which is 0 or 1 is not anything else. A world save is a heap
  of chunks with no index to the thing we want.
- Read once per save: a file whose size and modification time have not moved
  is not decompressed again, because a world saves every half hour and this
  is four megabytes.
- Uploaded characters still work, for worlds with no table.
- **Maps are compressed now, and Skald could not read them.** Valheim moved
  the map into a gzip stream and started keeping a second grid beside the
  first — what you uncovered, and what others uncovered for you. A character
  created since writes every map that way, so one that plays only on a
  server looked, to a reader that knew only the older shape, like someone
  who had never been anywhere. Both shapes now read.
- The two grids are **added together**, because a square someone else
  revealed for you is a square you can see, and this is a map of where a
  group has been.
- **Pins are optional rather than fatal.** Their shape gained a field that
  is not pinned down; a map with no pins is worth having, and a parser that
  refuses the map because it could not read a label is not.

## 0.6.1

- **A newly understood line now reaches the log that already held it.**
  Adding a pattern only ever matched lines that arrived *after* the upgrade:
  each file's read offset already said "done", so nothing went back for the
  rest. Raid history shipped in 0.6.0 and found nothing, because every raid
  line in the logs had been read and discarded months before Skald knew what
  one was.
- Skald now records a digest of the patterns it understands. When that
  changes, every event file is read again from the start — which costs a
  little time and adds nothing twice, since ingestion is keyed by the line's
  own text. Keyed on the patterns rather than the version, so a release that
  changes no pattern re-reads nothing.
- This is what made keeping the whole server log worth anything. Without it
  the promise — that a new pattern is a Skald upgrade and nothing else — was
  not true.
- **The map upload could not read a real character file.** It was written
  from a published description of version 33; files written today say
  version 46, and the layout moved. Every real upload was refused. Fixed,
  and checked against actual files for the first time.
- The parser no longer walks the file to the map — it **finds** it. The
  explored bitmap is unmistakable (edge × edge bytes, every one 0 or 1, four
  megabytes of it for a 2048-square map), and everything needed is read from
  around it. The fields in front of it have already been rearranged once;
  the bitmap is the one part whose shape is fixed by what it is. Version 33
  files still read, which is the point.
- Two corrections that came from real data: the map is a **length-prefixed
  byte array**, not inline fields; and an **optional point that is not set
  still occupies its twelve bytes**, zeroed. Assuming otherwise put the
  world id in the wrong place and read two of three worlds as id 0 — and
  since the id is the key a map is stored under, they would have overwritten
  each other.
- Character files are commonly **not** under `AppData`: with cloud saves
  they live in Steam's `userdata/<id>/892970/remote/characters`. Both paths
  are now given on the upload form and in the docs.

## 0.6.0

- **Raid history.** The server logs every raid as it starts, with an exact
  time — `Random event set:army_bonemass` — so the dashboard now lists what
  came for you, when, and who was online for it. No game-server change was
  needed: Skald already reads the whole log.
- The docs said raids were not in the log at all. That was true of the
  *filtered* log the hook used to write; capturing everything is what
  exposed them.
- The raid list comes from the game's own asset bundles, not memory: the ten
  the core event list references, plus the Mistlands, Ashlands, Deep North
  and mountain-cave raids from their biomes' location lists. Each one also
  has an `event_<name>_start`/`_end` localisation pair, which is how we know
  the list is the game's. An unknown id still reads as something.
- `Random event set:` with nothing after it is the event *ending*, and is
  not recorded as a raid starting.
- New `/api/raids`.
- **Four milestone keys named**, taken from the game's own asset bundles:
  `defeated_frozenking`, `defeated_frozenking_p3`, `defeated_hive` and
  `killed_frysling`. All Deep North, which is unfinished — so nobody can set
  them yet, and that is exactly why they are worth naming before anyone can.
  The list came from every Character prefab's `m_defeatSetGlobalKey`, which
  is where these live: they are not in the code, which is why
  `defeated_writhan` was in our worlds and nowhere in the assembly.
- They are deliberately **not** kind `boss`, however much FrozenKing looks
  like one. The badge row is driven by `BOSSES`, and a `boss` missing from
  that list is filtered out of the table *and* absent from the badges — it
  would disappear entirely. A test now enforces that invariant.
- **`bosshildir1`–`3` marked unverified.** Unlike every other key, they
  appear in neither the assembly nor any prefab, and no world of ours has
  one. Kept, since a key that never arrives shows nothing, but no longer
  presented as evidence.
- Generated labels no longer contain a double space (`killed_seekerbrood`
  read as "First  seekerbrood killed"), and a key that is nothing but a
  prefix no longer renders with a leading one.


- **Milestones are newest first**, on the page and in `/api/milestones`.
  They were the only list in Skald running the other way — `/api/sessions`
  and `/api/deaths` have always been newest first — so the kill you just
  made was at the bottom of the table.
- Keys found in the same scan share a timestamp, so the tie is broken
  deterministically and the table no longer reshuffles between refreshes.
- **Tell me about it.** Sign in, paste a Discord webhook, and Skald posts
  when someone comes online or a boss falls. No bot, no gateway, no
  dependency — one POST of one JSON field, from the poller that was already
  watching.
- **Only Discord webhook URLs are accepted.** Letting a signed-in visitor
  choose where the server sends a request is a forgery hole by
  construction: without the restriction, anyone with a Steam account could
  point Skald at a machine behind its firewall and use it as a prod.
- Nothing announces a backlog. The first tick after a restart only records
  what is true; a subscription made today does not replay the week.
- You are never told that you have arrived — a subscriber's own claimed
  characters are skipped.
- A webhook deleted in Discord answers 404 for ever, so failures are
  counted and the subscription switches itself off after ten rather than
  posting into the void every minute. The error shows on `/me`.
- **The map.** Upload a character file on `/me` and Skald keeps your fog of
  war and your pins; `/map` shows everyone's, added together, with the share
  of the world the group has seen between them.
- **It keeps nothing else because it decodes nothing else.** The per-world
  map is the second chunk of a `.fch`, right after five integers — so the
  parser reads the header and the worlds and *stops*. Inventory, skills,
  appearance, journal and name all sit after the part it reads and are never
  looked at. That is the privacy promise made structural rather than
  promised.
- Stored at a bit a pixel and deflated: a 2048-square map is a few kilobytes
  once compressed, not four megabytes.
- The PNG is written by hand — a 1-bit paletted image, which is exactly what
  the data already is. No image library, and Skald still has no
  dependencies.
- Hostile files are bounded rather than trusted: a map edge, a pin count and
  an upload size are all capped, and anything unreadable comes back as a
  sentence rather than a stack trace.
- New `/map` and `/map.png?world=<uid>`.

## 0.5.1

- **A server log kept in the events directory was read twice.** That is the
  sensible place for it — the volume is already shared with the game and
  already survives a container being recreated — but the file matches the
  glob that finds the hook's files, so it was read as a whole server log
  *and* as a hook file. The data was unharmed (ingestion is keyed by the
  line's own text), but every line of ordinary server chatter counted as one
  Skald had failed to recognise, and that number is the entire "an update
  reworded something" signal on `/diagnostics`. A file named as a world's
  `log_file` is no longer picked up as a hook file, and a count left behind
  by an older version is cleared.
- **"Default settings" and "Modified" never appeared on the page.** The
  dashboard was reading the summary built for the online list, which does
  not carry whether the server calls the world modified. Worlds with
  modifiers in the log were unaffected; worlds relying on the Steam tag
  showed nothing at all.

## 0.5.0

- **How the world is set up**, under the tabs: combat, death penalty,
  resources, raids and portals, or the name of the preset the server was
  started with. Verified against a real server rather than guessed.
- Two sources, because neither is enough alone. The server logs its
  modifiers **in words**, once, at startup — that is where the names come
  from. It also advertises them in its Steam tags as `m=`, which Skald reads
  only as a **yes or no**: the ids in it are undocumented, built at runtime
  and free to be renumbered by any update, and a confidently wrong
  "Very Hard" is worse than no answer. Between them, the tag says *whether*
  a world is modified even when its startup went unwatched, and the log says
  *which* — and the page says so plainly when it knows the first and not the
  second.
- A preset is logged as itself and is **not** expanded into the individual
  settings, so a world reports whichever its operator used.
- Modifiers are logged once per run, so the newest start wins and an older
  one is history. A modifier Skald has never heard of still shows, with a
  tidied-up label.
- New `/api/world`: per world, its modifiers, preset, game version and
  whether the server calls it modified.

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
