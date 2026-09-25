"""A server's whole log, kept on the same volume as the hook's files.

That is the sensible place for it -- the volume is already shared and
already survives a container being recreated -- but it means the file
matches the glob that finds hook files, and the two are read very
differently. These tests are about not reading it both ways.
"""
from tests.conftest import ALFR, WORLD, join, leave, line

from skald import app, config, store

T = 1_800_000_000


def configure(tracker, log_name):
    """Point the world at a log file inside the events directory."""
    events = tracker.dirs["events"]
    path = str(events / log_name)
    cfg = app.CONFIG
    app.apply_config(config.Config(
        events_dir=cfg.events_dir, data_dir=cfg.data_dir, saves_root=cfg.saves_root,
        backups_root=cfg.backups_root, default_world=WORLD, timezone="UTC",
        worlds=(config.World(name=WORLD, status_url="http://127.0.0.1:1/s.json",
                             log_file=path),)))
    return path


def server_log(*groups):
    """What a real server writes: our lines, buried in chatter."""
    noise = [line(T + 5, "ZDOMan.LoadChunks => Connecting Portals done [12ms]"),
             line(T + 6, "Ngui menu state changed to 0"),
             line(T + 7, "AsyncResourceUpload failed.")]
    return [ln for g in groups for ln in g] + noise


def test_a_named_log_is_not_also_read_as_a_hook_file(tracker):
    path = configure(tracker, f"{WORLD}.full.log")
    assert path not in app.event_paths()
    assert app.event_paths() == []


def test_the_hooks_own_file_is_still_read(tracker):
    """Naming one file must not switch the others off."""
    configure(tracker, f"{WORLD}.full.log")
    (tracker.dirs["events"] / f"{WORLD}.log").write_text("\n")
    assert [p.rsplit("/", 1)[-1] for p in app.event_paths()] == [f"{WORLD}.log"]


def test_server_chatter_is_not_counted_as_unrecognised(tracker):
    """The count on /diagnostics means "Valheim reworded a line". Ordinary
    noise in a whole server log must not go anywhere near it."""
    path = configure(tracker, f"{WORLD}.full.log")
    with open(path, "w") as f:
        f.write("\n".join(server_log(join(T, ALFR), leave(T + 3600, ALFR))) + "\n")
    app._CACHE.update(sig=None, history=None)
    app.ingest_events()
    assert sum(store.skipped_lines(app.db()).values()) == 0
    # and the lines that matter did land
    assert len(app.history()["sessions"]) == 1


def test_a_count_from_before_the_file_was_named_is_cleared(tracker):
    """Upgrading is what this is for: the file was read as a hook file
    first, and carried a count that never meant anything."""
    path = configure(tracker, f"{WORLD}.full.log")
    with open(path, "w") as f:
        f.write("\n".join(server_log(join(T, ALFR))) + "\n")
    # Pretend the earlier version had counted its chatter.
    with app.db() as conn:
        conn.execute("INSERT INTO files (path, size, mtime, skipped)"
                     " VALUES (?, 0, 0, 99)", (path,))
    app.ingest_events()
    assert sum(store.skipped_lines(app.db()).values()) == 0


def test_the_page_says_default_when_the_server_says_so(tracker):
    """render() reads the status that carries `modified`, not the summary
    built for the online list -- which does not."""
    h = tracker.write_events(join(T, ALFR))
    app.STATUS[WORLD] = {"up": True, "count": 0, "status_ts": T, "error": None,
                         "game_version": "1.0.16", "modified": False}
    page = app.render(h, T + 60, WORLD)
    assert "Default settings" in page


def test_the_page_flags_a_modified_world_it_never_saw_start(tracker):
    h = tracker.write_events(join(T, ALFR))
    app.STATUS[WORLD] = {"up": True, "count": 0, "status_ts": T, "error": None,
                         "game_version": "1.0.16", "modified": True}
    page = app.render(h, T + 60, WORLD)
    assert "Modified" in page
