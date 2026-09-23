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
- [x] Every fixture invented. No real player names, Steam IDs or world names
      are in this repository, and none should ever be.

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

## M2 — v0.1, the first release worth sharing

- [ ] Multi-arch image (amd64, arm64) published to GHCR
- [ ] Quick start that works from a clean machine
- [ ] Docs: install, configuration reference, how it works, and an honest
      limitations page
- [ ] Versioned releases and a changelog

## M3 — Sign in through Steam (optional)

Identity only: the site stays as readable as you configure it, and signing in
adds personal features. Nothing changes for people who do not.

- [ ] OpenID 2.0 — Steam does not offer OAuth2 for third-party sites; sign-in
      returns a SteamID64 and nothing else
- [ ] Claim your character, so a Steam account and a character name are linked
- [ ] A personal view: your playtime, your deaths, your sessions
- [ ] Steam Web API key (optional) for display name and avatar

## M4 — The map

- [ ] Upload a character file; keep only the fog of war and the map pins and
      discard the rest
- [ ] Merge everyone's exploration into one map of what the group has seen
- [ ] Maybe: render terrain from the world seed, so the fog sits on a real map

## M5 — Later

- [ ] Read a plain log file, for vanilla and systemd servers — the biggest
      gap in reach, and likely to jump the queue
- [ ] Read container logs through the Docker socket (off by default; it is a
      powerful socket to hand to a web app)
- [ ] Prometheus metrics
- [ ] Discord or webhook alerts when a boss falls
- [ ] Translations

## Not planned

- Anything that writes to your world, or needs a mod installed in the game
- Anything that needs the game's own assets or art
