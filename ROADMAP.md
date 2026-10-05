# Roadmap

Skald began as one service in a homelab deploy repo and is being pulled out
into something other people can run. This is the plan and its current state.

## M0 — Extract ✅

The code as it was, plus the tests it never had.

- [x] Repo, MIT licence, attribution kept for the weather port
- [x] Code moved into a `skald` package, runnable as `python3 -m skald`
- [x] Tests: sessions and deaths, save parsing, milestone dating, the world
      clock, page rendering, and the weather engine against the reference
      implementation's own vectors
- [x] CI: lint, tests on 3.11 and 3.14, and an image build that has to answer
      `/healthz` before it counts
- [x] Every fixture invented. No real player names or Steam IDs are in this
      repository, and none should ever be. The screenshots are from the
      author's own servers, so they carry real world names and seeds — the
      map shows nobody's name, because a corpse's owner sits in an attribute
      the page reveals on tap rather than in the picture.

Writing the tests found three real bugs, all fixed: a session whose
`Closing socket` line never arrived was merged with a much later one and the
hours between counted as play; the forecast's footnote named biomes the world
had not reached; and a world whose bosses fell before Skald was watching
showed no badges at all.

## M1 — Make it general ✅

The work that turns "runs in my homelab" into "runs in yours".

- [x] Config file instead of environment variables, with worlds, paths and
      timezone in one place
- [x] SQLite with migrations, replacing the JSON state files, and an importer
      for `milestones.json`
- [x] Run as a non-root user
- [x] A diagnostics page: what Skald can read, what it cannot, and why
- [x] Handle Valheim updates gracefully — the game version each server
      reports, the save format's version, and a count of log lines that match
      nothing, all on `/diagnostics`

## M2 — v0.1, the first release worth sharing ✅

- [x] Multi-arch image (amd64, arm64) published to GHCR
- [x] Quick start that works from a clean machine (verified on a fresh
      Ubuntu VM, which found three bugs that made it fail outright)
- [x] Docs: install, configuration reference, how it works, and an honest
      limitations page
- [x] Versioned releases and a changelog

## M3 — Sign in through Steam (optional) ✅

Identity only: the site stays as readable as you configure it, and signing in
adds personal features. Nothing changes for people who do not.

- [x] OpenID 2.0 — Steam does not offer OAuth2 for third-party sites; sign-in
      returns a SteamID64 and nothing else
- [x] Claim your character — and it claims itself: the log already pairs a
      connection's SteamID with the character on it, so `/me` offers only
      the characters that account has played, and the most-played one is the
      primary unless you choose otherwise (0.3.0)
- [x] A personal view: your playtime, your deaths, your sessions, and where
      you stand among everyone (0.4.0)
- [x] Steam Web API key (optional) for display name and avatar — with one
      a signed-in player shows their Steam name and picture, without one the
      last four digits of their ID. Read at sign-in, so a key added later
      takes effect on the next one.

## M4 — The map ✅

- [x] Upload a character file; keep only the fog of war and the map pins and
      discard the rest — structurally, since the map is read before the
      player and the parser stops there. **Since removed**: the cartography
      table made it pointless, and an upload nobody needs is a risk nobody
      needs.
- [x] Merge everyone's exploration into one map of what the group has seen
      — and then discover the game already does it, in the cartography
      table, which the world save carries (0.7.0)
- [x] Render terrain from the world seed, so the fog sits on a real map
      (0.11.0). Valheim stores no terrain; it rebuilds the world from one
      number, so Skald does the same arithmetic. Checked against the game's
      own map until biomes and coastlines agreed.
- [x] Layers over it, each a checkbox: pins by kind, portals with a line to
      the other end when you tap one, where people have built, corpses
      nobody has fetched including the ones in caves, boats, and stumps
- [x] Each boss altar with its own mark and its name (0.23.0)
- [x] Sunken crypts, marked at their entrances, for the ones the world has
      generated (0.25.0)
- [x] A night view: the ground dimmed and construction drawn as lights, with
      fires weighted over walls, on an absolute log scale so two worlds can
      be compared (0.22.0)
- [x] Ore still in the ground, off unless the owner turns it on — it retires
      the wishbone, so it is their call rather than Skald's
- [x] Fog off is the server's decision too, and the map never flashes
      unfogged while it loads (0.21.0)
- [x] Explored share measured against the playable disc rather than the
      square it is drawn in — the corners are past the edge of the world
      (0.21.0)
- [x] Pinch to zoom, so the map works on the phone it is mostly read on
      (0.23.0)

### Known gaps in it

- [ ] Merge every cartography table in a world. Skald shows the one with the
      most explored, so in a world with several, writes to a smaller one are
      invisible.
- [ ] Pins fail to parse on some older save layouts, and fail *silently* —
      the layers simply do not appear, which reads as a world without pins
      rather than as a failure. It should say so.
- [ ] Pair portals properly. The save records a tag and a creator but no
      target, so Skald draws a line to every portal sharing a name rather
      than to the one it actually leads to.

## M5 — Later

- [x] Read a plain log file, for vanilla and systemd servers (0.2.0) — and
      a container's json log, unwrapped, though its path moves whenever the
      container is recreated
- [x] Discord alerts when a boss falls (0.6.0). Discord webhook URLs only,
      deliberately: a signed-in visitor choosing the address means a stricter
      check is worth more than supporting every endpoint.
- [ ] Prometheus metrics
- [ ] Translations

## Not planned

- Anything that writes to your world, or needs a mod installed in the game
- Anything that needs the game's own assets or art
