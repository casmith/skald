# Changelog

## 0.25.1

- **The sunken crypts layer starts unticked.** On real worlds it found 56,
  138 and 157, not the handful expected: once a swamp has been generated, it
  is thick with crypts, and that many headstones crowd out the marks people
  placed themselves. Tick the box to see them.

## 0.25.0

- **Sunken crypts on the map.** Each one gets a swamp-green headstone where
  its entrance is, plus a box in the legend with the count, ticked by
  default. A world has only a handful of crypts and each is worth a trip,
  so the layer earns its space in a way the stumps never did.
- They are found by the iron gate across each entrance. The crypt itself
  isn't a world object; the game keeps locations in a separate list. Every
  crypt has exactly one gate, though, and the gate is an object. The inside
  of a crypt is built five thousand metres up, like a cave's, so anything
  found up there is left out and the mark goes on the swamp, where you
  walk in.
- These are the crypts the server has *generated*, which only happens once
  somebody has been nearby. So the layer follows exploration and can't be
  used to scout ahead, the same as the fog.

## 0.24.2

- **A three-pixel halo on the night lights, down from five.** A pixel is
  twelve metres and a building piece is about two, so even one overstates
  it; five turned every cluster into a soft bubble sixty metres across and
  ran a row of houses into one blob. Half as many pixels are lit now and
  structures read as structures — a long wall as a line, a hall as a
  rectangle, a hut on a headland as a point rather than a smudge.
- The brightness ceiling came down with it, to 1900, so nothing dims. The
  kernel's weights fell to 40 from 96, but the peak only falls to about 0.68
  of what it was, because a tighter stamp also stops neighbouring pieces
  feeding each other's pixels. Scaling by the weights would have lit every
  settlement a step too bright; 0.68 is what two real worlds give at the
  resolution the map is drawn at.
- The day view keeps its wider halo. That one reads as firelight over a
  settlement, which is what it is for.

## 0.24.1

- **One pixel per stump, not a 36-metre cross.** The stump layer reused the
  ore layer's mark, which is deliberately bigger than a pixel so a vein can
  be found by eye. At 12 metres to the pixel that is 36 metres across for a
  thing about a metre wide, so twenty stumps in a clearing merged into a blob
  reaching past the trees that were ever there -- a felled field read as a
  carpet, including ground that had been cleared of its stumps too. The ore
  layers keep the cross.
- The counts themselves are unchanged. They were checked after the report:
  every position is distinct, none collides with another object's, and the
  two things that might have marked a bad read -- no health field, and
  sitting off the height the seed gives -- turn out to be statistically
  independent of each other, which is what two unrelated noise sources look
  like rather than two detectors of the same fault. The second has an
  innocent explanation anyway: terraforming moves the ground out from under
  the generated height.

## 0.24.0

- **Deforestation, tracked over time.** How many stumps a world holds,
  sampled on each save scan and kept as a series, with the change over the
  last week and a sparkline. A stump carries no record of who felled it — it
  is a destructible, not a built piece, so the game sets no creator on it —
  so the tally is the world's and nobody's in particular. Checked against
  1,375 real stumps across three worlds: a creator sat in the slot a build
  piece keeps one in exactly zero times.
- **Stumps on the map**, as a layer, off by default. A thousand of them over
  a forest is a stain, and most visits are not about the logging.
- One scan loop instead of six. Each reader had its own copy keyed on
  `(path, size, mtime)`, which grew an entry per chunk rewrite, dumped the
  whole cache at a size limit, and made the pass after that re-read the
  entire world. Now keyed on the path alone and bounded by the directory, so
  that sawtooth is gone — it was the shape of the I/O that stalled a host
  once already, and it got worse with every reader added.

## 0.23.0

- **Each boss gets its own mark on the map, and its name beside it.** Every
  boss pin was the same skull with the name hidden in a tooltip, so a world
  with ten altars showed ten identical marks and answered "which one?" one
  hover at a time. Eikthyr is a bolt, the Elder a tree, Moder a snowflake,
  Yagluth a meteor, the Queen a crown; Bonemass keeps the skull. A boss the
  game adds later falls back to the skull and its tooltip.
- A pin crossed off in game — how Valheim records the kill — now strikes
  through the name as well, so a beaten boss reads as beaten.
- **Pinch to zoom on a phone.** The viewer turns off the browser's own touch
  gestures so it can drive the pan itself, which also turned off pinch-zoom
  — and zooming was on the wheel, which a phone does not have. The map was
  stuck at 1x on mobile with no way out. Two fingers now zoom and pan at
  once, and lifting one carries on panning from the finger still down.

## 0.22.1

- A tighter halo on the night lights. The wide stamp reads as firelight over
  a settlement, which is what the day view wants, but from orbit a town is a
  point and a soft edge four hundred metres across turned a village into a
  smudge. The brightness steps are unchanged; the scale was recalibrated to
  the smaller stamp so a city still burns white.

## 0.22.0

- **The map at night.** A second view of the same world with the ground
  dimmed and the building drawn as lights, the way the dark side of the
  earth looks from orbit. A settlement of a couple of thousand pieces burns
  white; a hut on a headland is one faint ember, but it is there.
- Fires count for more than walls — hearths, torches, forges, kilns and
  braziers are weighted six to one, since what you would see at night is
  what is burning.
- The scale is absolute and logarithmic, so two worlds can be compared and
  the range from a hut to a capital fits in eight steps.
- At night only the lights are on. Two hundred portal labels over a photograph
  of a city is not the view — but they are boxes, not decisions, so tick one
  and it comes back.

## 0.21.0

- The map never appears without its fog, even for a moment. The terrain and
  the fog are separate pictures and whichever arrives first is what you see,
  so a cached terrain beside an uncached fog showed the whole world. Nothing
  is drawn until the fog is on it.
- **Whether anyone may take the fog off is now the server's decision.**
  `SKALD_ALLOW_FOG_OFF=0` refuses it, and refuses it on the server rather
  than by leaving the link out — a query string is not a permission. Allowed
  by default, which is what it did before.
- The explored figure counts the world, not the picture. The map is drawn
  square but Valheim's world is a disc ending 10,500 metres out, and the
  corners are somewhere nobody can sail to — so every figure was about 1.7
  times smaller than the truth. A world reading 3.84% was really at 6.44%.

## 0.20.2

- Fixed: clicking a portal stopped drawing the line to its other end. The
  corpse and boat layers are drawn above the portals and each covers the
  whole map, so they took the click before it reached the marker. Only the
  markers take clicks now; the layers they sit in do not.

## 0.20.1

- A world Skald has not seen before no longer reads its entire backup
  history in one pass. Every backup is new to it, and reading them back to
  back off a NAS is enough to starve the machine the game servers run on.
  Six per scan now, catching up over the following minutes.
- Only the head of a backup's save is read. The milestone keys sit in the
  first few hundred bytes and the rest is chunk data, so a mature world's
  save no longer comes across the network in full — tens of megabytes per
  backup, for nothing.
- Fixed: the cartography cache emptied itself on every file, so only the
  last one stayed cached and every pass re-read and re-decompressed the
  whole world.

## 0.20.0

- The map shows the world's day, time and whether it is dawn, day, dusk or
  night. The clock only runs while someone is online, so it is the world's
  own day, not elapsed real time.
- The dashboard's map link opens the world you were reading. It used to open
  whichever world the map listed first.

## 0.19.0

- Boats on the map, by kind: raft, karve, longship, drakkar.
- Fixed: the map printed "indoors" instead of saying where the map came
  from. A corpse marker had taken over the variable holding the description.
- Removed the map's leftover link to the character upload, dropped in 0.16.0.
- `tools/demo.py` builds a world as well as a month of history — a seed, a
  cartography table, portals, boats, a village and a corpse — so the map
  works without a server. The screenshots are made from it.

## 0.18.0

- Corpses on the map, with whose they are. A tombstone lasts until someone
  loots it, so these are the ones still out there.
- Corpses inside caves are included. Valheim builds cave interiors five
  thousand metres above their own entrance, so the position is the ground
  you walk to; they are marked as indoors.

## 0.17.0

- Where the ore still is: silver, copper, tin and obsidian.
- Off unless `SKALD_SHOW_ORES=1`. Without it the saves are not scanned and
  the legend does not mention it. Silver is meant to be hunted with a
  wishbone; whether to retire that is the server owner's call. The layers
  start unticked even when enabled.
- A mined-out deposit disappears, because it disappears from the save. A
  vein is mined in pieces, so a marker means something is left rather than
  that it is untouched.

## 0.16.0

- The dashboard links to the map. It stays a separate page: the dashboard
  reloads every minute, which would throw away your pan and zoom.
- Removed the character file upload. A world's cartography table gives a
  better map and needs nothing from anybody. The form, both endpoints and
  the storage behind it are gone; the unused table stays in the schema,
  since schema steps are applied by position.

## 0.15.0

- Tap a portal to draw a line to its other end. Tapping a group draws one
  for every tag under it.
- The pairing is the tag, because that is all the save holds: a portal
  record has a tag, a creator and sometimes a health, and no reference to
  the other end. The game pairs them by tag on load.
- A portal whose partner is gone draws nothing.

## 0.14.1

- Dragging the map no longer selects the labels it crosses.

## 0.14.0

- A legend under the map with a checkbox per layer: portals, building, and
  pins split by kind, each with its count.
- Toggling is immediate and does not move the map. These were links, and a
  reload came back at the top left at 1x.
- Pins used to be one switch for all of them; each kind is now its own.
- Fog of war stays a link, since it changes what the server draws.

## 0.13.1

- Fixed: 0.13.0 drew no portals at all. Building a marker for a lone named
  portal passed the portal where its name was wanted, which threw and
  stopped the drawing.
- The tests now run the page's script against a stub browser and check
  markers come out. The grouping had its own tests and they passed while the
  map was blank.

## 0.13.0

- Portal markers group by how far apart they look, and regroup as you zoom.
  Two portals twenty metres apart are four screen pixels apart at 2.5x and a
  clear gap at 16x, so the grouping cannot be settled when the page is built.
- Tapping a group names everything under it.

## 0.12.1

- Fixed: the names in a group of portals never appeared. They were on a
  `title`, which cannot fire while the map holds the pointer, and which a
  phone has no way to show at all. Tap or click instead.
- The map's script is syntax-checked in the tests. This release nearly
  shipped a broken string literal, which would have stopped the panning and
  zooming too, and the page would still have returned 200.

## 0.12.0

- Portals that sit on top of each other are drawn as one marker with a
  count: 32 portals became 15 markers on the world this was built against.
- Each portal joins the nearest group rather than the first within reach, so
  a row of portals does not chain into one group spanning the map.

## 0.11.0

- Where you have built, drawn as firelight: bright where a settlement is
  dense, faint at an outpost.
- It shows building rather than masonry. Valheim's ruins use the same
  prefabs people build with and there are far more of them — fifty thousand
  generated pieces against a few thousand built ones on one world. A placed
  piece records who placed it; a generated one does not.

## 0.10.1

- A world being drawn says so, with how far along it is, and the page
  reloads until it arrives. Drawing used to happen inside the request, which
  showed an empty square for six minutes.
- Drawing moved to one background worker doing one world at a time. They are
  threads of one process, so the GIL gives them a single core between them
  however many the machine has.

## 0.10.0

- Portals on the map, with their names.
- Two portals sharing a name are the two ends of one, which is how the read
  checks itself: nothing in the parsing pairs them up, and on the world this
  was built against all 14 names came out as pairs.

## 0.9.0

- The pins from the cartography table: 519 on the world this was built
  against, 289 named by hand. Nothing is uploaded.
- Crossed-off pins are faded; the game's own pins say `Eikthyr` rather than
  `$enemy_eikthyr`.

## 0.8.0

- Real terrain, generated from the world seed. Valheim stores no terrain; it
  rebuilds the world from one integer, so drawing a map means doing the same
  arithmetic.
- Zoom and pan, zooming about the pointer.
- The ground is drawn, not the biome. A base height decides which biome
  stands somewhere; each biome then shapes its own terrain, and the game
  colours water by comparing that against sea level. Three quarters of the
  world is not Ocean biome but only two fifths of it is dry, so colouring by
  biome alone turns an archipelago into a continent.
- Checked against the servers rather than against itself: 97.1% of 2,221
  placed landmarks stand on dry land, land area comes to 41–42% against a
  published 38–41%, and 93.1% of the explored pixels on a real in-game map
  read as land.
- Drawn once per seed and kept on disk. The filename records how it was
  drawn, so improving the drawing replaces what is kept.
- Reads `.fwl2` world metadata as well as `.fwl`.

## 0.7.1

- Fixed: a line was counted twice when the log hook quoted it. The hook
  announces its work by quoting the whole line, so a full log holds every
  hooked line twice, and the copy differs by one trailing quote — enough to
  slip past the primary key.
- Deaths were the casualty; they drive the per-player table and a chart. On
  the deployment that found this, 316 of 2,834 events were copies and none
  was a death. Copies with a genuine twin are removed on upgrade.

## 0.7.0

- The group's map, with nothing to upload. A world's save holds its
  cartography table, and Skald already reads those saves. The table held
  154,355 explored pixels on one world against 3,920 in the fullest
  character file.
- Found by shape: a run of `edge × edge` bytes that are all 0 or 1 is not
  anything else. Read once per save.
- Compressed maps now read. Valheim moved the map into a gzip stream and
  added a second grid beside the first, so a character made since looked
  empty to a reader that knew only the older shape. The two grids are added
  together.
- Pins are optional rather than fatal: a map with no pins is worth having.

## 0.6.1

- Fixed: a newly understood line never reached the log that already held it.
  Adding a pattern only matched lines arriving after the upgrade, because
  each file's read offset already said "done". Raid history shipped in 0.6.0
  and found nothing.
- Skald records a digest of the patterns it understands and re-reads every
  file when it changes. Keyed on the patterns rather than the version, so a
  release changing no pattern re-reads nothing.
- Fixed: the map upload could not read a real character file. It was written
  from a description of version 33; files written today say 46 and the
  layout moved.
- The parser finds the map rather than walking to it, and reads what it
  needs from around it.
- Character files are commonly not under `AppData`: with cloud saves they
  live in `userdata/<id>/892970/remote/characters`.

## 0.6.0

- Raid history: what came for you, when, and who was online. The server logs
  every raid as it starts, so no game-server change was needed.
- The raid list comes from the game's own asset bundles, not memory.
- Four milestone keys named from those bundles: `defeated_frozenking`,
  `defeated_frozenking_p3`, `defeated_hive`, `killed_frysling`. All Deep
  North, which is unfinished, so nobody can set them yet.
- `bosshildir1`–`3` marked unverified: they appear in neither the assembly
  nor any prefab.
- Milestones are newest first, on the page and in `/api/milestones`.
- Discord webhooks: sign in, paste one, and Skald posts when someone comes
  online or a boss falls. Only Discord webhook URLs are accepted — letting a
  signed-in visitor choose where the server sends a request is a forgery
  hole. A new subscription does not replay the week, and one that has failed
  ten times switches itself off.
- The map: upload a character file and Skald keeps your fog of war and your
  pins. It decodes nothing else — inventory, skills, appearance and journal
  all sit after the part it reads.
- New `/api/raids`, `/map`, `/map.png`.

## 0.5.1

- Fixed: a server log kept in the events directory was read twice, once as a
  whole log and once as a hook file. The data was unharmed, but every line
  of ordinary chatter counted as one Skald had failed to recognise — which
  is the entire "an update reworded something" signal on `/diagnostics`.
- Fixed: "Default settings" and "Modified" never appeared on the page.

## 0.5.0

- How the world is set up: combat, death penalty, resources, raids and
  portals, or the preset the server was started with.
- Two sources. The server logs its modifiers in words at startup, which is
  where the names come from; its Steam tags say only whether a world is
  modified, since the ids in them are undocumented and free to be
  renumbered. The page says so plainly when it knows one and not the other.
- New `/api/world`.

## 0.4.1

- Fixed: wind direction was backwards. Skald showed the direction the wind
  came from; Valheim's bearing is the direction it blows toward. The weather
  engine was right and only the page turned it around.
- **API change:** `wind_from` is now `wind_dir` in `/api/weather`.

## 0.4.0

- Your own page: hours over 24 hours, 7 days, 30 days and all time, deaths
  and deaths per 10 hours, longest session, where you stand among everyone,
  and your last few sessions.
- It narrows the dashboard's own arithmetic rather than repeating it, so
  your page cannot drift from the table you appear in.

## 0.3.0

- Your characters, worked out rather than claimed. A Valheim log names
  characters, not accounts, but a connection logs a SteamID and the
  character that follows it. Your most-played character becomes your primary
  automatically.
- There is nothing to type in: on a public instance a free-form claim would
  let anyone take any name.
- Your characters are private — never on the dashboard, never in the API,
  and no Steam ID ever is.

## 0.2.0

- Skald no longer needs the lloesche image. A world can name its own
  `log_file`, and Skald reads the server's whole log, unwrapping a
  container's json format if it finds one. Vanilla and systemd servers work.
- Optional sign in through Steam, off until `base_url` is set. It is OpenID
  2.0, so there is nothing to register and no client secret; what comes back
  is a SteamID64 and nothing else. Sessions are a random token in an
  HttpOnly, SameSite=Lax cookie, and the database keeps only its hash.
- Fixed: a bind mount whose source has gone leaves an empty directory, which
  Skald counted as an existing but empty log.

## 0.1.1

- Boss kills are dated to the save interval, and the docs say so. Valheim
  does not log a kill: the `Setting global key` message is compiled out of
  release builds. Verified by killing Eikthyr on a test server.
- `/diagnostics` shows how often each world actually saves, since that is
  the precision every boss kill is dated to.

## 0.1.0 — first release worth sharing

The first version documented well enough for someone else to run, and tested
by doing exactly that on a clean machine.

- The quick start works from nothing. A fresh Docker volume is root-owned
  and Skald runs unprivileged, so it could not create its database and every
  page failed — while the container reported healthy, because `/healthz`
  only proved a socket was open.
- Docs: [installing](docs/install.md), [configuration](docs/configuration.md),
  [how it works](docs/how-it-works.md), [limitations](docs/limitations.md).
- `tools/demo.py` builds a month of invented history, for trying Skald
  without a game server and for screenshots.

## 0.0.2

- Configuration from a TOML file, with environment variables over it. The
  older `TRACKER_*` names still work.
- `/diagnostics`: every path and world, what is arriving and what is not,
  and where each setting came from.
- SQLite under `data_dir`, with migrations. Every line is ingested once and
  kept, so history outlives the log files.
- Runs as uid 10001 instead of root.
- Notices when Valheim moves: game version, save format version, and a count
  of log lines matching nothing.

## 0.0.1

Extracted from the homelab deploy repository it grew up in, with the tests it
never had. Writing them found three bugs:

- a session whose `Closing socket` never arrived was merged with the next
  one, counting the hours between as play;
- the forecast's footnote named biomes past the progress gate;
- a world whose bosses fell before Skald was watching showed no badges.
