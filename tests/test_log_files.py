"""Reading a server's own log, for servers without the hook.

A vanilla or systemd Valheim server writes the same lines Skald wants, mixed
in with everything else it says. A container's json log wraps each line in an
object. Both are read the same way as the hook's files.
"""
import json

from tests.conftest import ALFR, BERA, WORLD, join, leave, line

from skald import app, config

T = 1_800_000_000

# What a real server log looks like: the lines that matter, buried.
NOISE = [
    "Sep 23 15:59:46 supervisord: valheim-server (Filename: ./Runtime/Export/Debug/Debug.bindings.h Line: 35)",
    "Unloading 5 Unused Serialized files (Serialized files now loaded: 0)",
    "Sep 23 16:00:00 supervisord: valheim-updater DEBUG - [42] - Checking for updates",
]


def use_log_file(tracker, path, world=WORLD):
    """Point the world at a server log instead of the hook's file."""
    cfg = app.CONFIG
    worlds = tuple(config.replace(w, log_file=str(path)) if w.name == world else w
                   for w in cfg.worlds)
    app.apply_config(config.replace(cfg, worlds=worlds))


def test_events_are_found_in_a_noisy_server_log(tracker):
    log = tracker.dirs["events"].parent / "server.log"
    lines = NOISE[:1] + join(T, ALFR) + NOISE[1:] + leave(T + 1800, ALFR)
    log.write_text("\n".join(lines) + "\n")
    use_log_file(tracker, log)

    h = app.history()
    (s,) = h["sessions"]
    assert (s["player"], s["start"], s["end"]) == (ALFR, T, T + 1800)


def test_a_container_json_log_is_unwrapped(tracker):
    """Container logs, without handing Skald the Docker socket."""
    log = tracker.dirs["events"].parent / "container-json.log"
    wrapped = [json.dumps({"log": ln + "\n", "stream": "stdout",
                           "time": "2026-09-23T16:00:00.000000000Z"})
               for ln in join(T, ALFR) + leave(T + 600, ALFR)]
    log.write_text("\n".join(wrapped) + "\n")
    use_log_file(tracker, log)

    (s,) = app.history()["sessions"]
    assert (s["player"], s["seconds"] if "seconds" in s else s["end"] - s["start"]) == (ALFR, 600)


def test_a_server_log_is_read_incrementally(tracker):
    log = tracker.dirs["events"].parent / "server.log"
    log.write_text("\n".join(NOISE + join(T, ALFR)) + "\n")
    use_log_file(tracker, log)
    app.history()

    with log.open("a") as f:
        f.write("\n".join(NOISE + leave(T + 900, ALFR)) + "\n")
    assert app.ingest_events() == 1          # only the leave line is an event
    assert app.history()["sessions"][0]["end"] == T + 900


def test_noise_is_not_counted_as_unrecognised(tracker):
    """A whole server log is mostly noise; counting it would say nothing."""
    from skald import store

    log = tracker.dirs["events"].parent / "server.log"
    log.write_text("\n".join(NOISE + [line(T, "Some line with a timestamp")]) + "\n")
    use_log_file(tracker, log)
    app.history()
    assert store.skipped_lines(app.db()) == {}


def test_a_hook_file_still_counts_its_misses(tracker):
    """The hook only writes what Skald asked for, so a miss is a signal."""
    from skald import store

    path = tracker.dirs["events"] / f"{WORLD}.log"
    path.write_text("\n".join(join(T, ALFR)) + "\n"
                    + line(T + 5, "A line from some future patch") + "\n")
    app.history()
    assert store.skipped_lines(app.db()) == {str(path): 1}


def test_both_sources_can_feed_different_worlds(tracker):
    """One world through the hook, another from its own log."""
    cfg = app.CONFIG
    other = "Utgard"
    log = tracker.dirs["events"].parent / "utgard.log"
    log.write_text("\n".join(join(T, BERA)) + "\n")
    app.apply_config(config.replace(cfg, worlds=cfg.worlds + (
        config.World(name=other, status_url="http://127.0.0.1:1/x", log_file=str(log)),)))

    (tracker.dirs["events"] / f"{WORLD}.log").write_text("\n".join(join(T, ALFR)) + "\n")
    h = app.history()
    assert {s["world"]: s["player"] for s in h["sessions"]} == {WORLD: ALFR, other: BERA}


def test_diagnostics_names_the_source(tracker):
    log = tracker.dirs["events"].parent / "server.log"
    log.write_text("\n".join(join(T, ALFR)) + "\n")
    use_log_file(tracker, log)
    d = app.diagnostics(app.history(), T + 100)
    assert d["worlds"][0]["events_file"] == {"path": str(log), "exists": True,
                                             "source": "server log", "sessions_seen": 1}


def test_a_missing_log_is_not_mistaken_for_an_empty_one(tracker):
    """A bind mount whose source has gone leaves an empty directory behind:
    Docker's container log paths change whenever a container is recreated."""
    gone = tracker.dirs["events"].parent / "vanished.log"
    gone.mkdir()                      # what Docker leaves in that case
    use_log_file(tracker, gone)
    assert app.ingest_events() == 0
    d = app.diagnostics(app.history(), T)
    assert d["worlds"][0]["events_file"]["exists"] is False
