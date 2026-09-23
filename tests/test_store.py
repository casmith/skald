"""The database: ingesting the hook's files, and keeping what we learn.

The event files are the source of new lines, but a poor long-term home --
they can be rotated, trimmed or lost. Every line is taken in once, keyed by
its own text, and everything downstream reads rows.
"""
import json

from tests.conftest import ALFR, WORLD, join, leave, stamp

from skald import app, store


def lines(tracker, *groups):
    return [ln for g in groups for ln in g]


def count(conn, world=WORLD):
    return conn.execute("SELECT count(*) FROM events WHERE world = ?", (world,)).fetchone()[0]


def test_the_schema_is_versioned(tmp_path):
    conn = store.connect(str(tmp_path))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(store.SCHEMA)
    # Migrating again is a no-op, not a second set of tables.
    assert store.migrate(conn) == len(store.SCHEMA)


def test_lines_are_taken_in_once(tracker):
    T = 1_800_000_000
    tracker.write_events(join(T, ALFR))
    conn = app.db()
    before = count(conn)
    assert before == 2
    # Same file, unchanged: nothing new, and nothing re-read.
    assert app.ingest_events() == 0
    assert count(conn) == before


def test_only_the_new_tail_is_read(tracker):
    T = 1_800_000_000
    path = tracker.dirs["events"] / f"{WORLD}.log"
    path.write_text("\n".join(join(T, ALFR)) + "\n")
    app.ingest_events()
    with path.open("a") as f:
        f.write("\n".join(leave(T + 600, ALFR)) + "\n")
    assert app.ingest_events() == 1
    assert count(app.db()) == 3


def test_a_partial_last_line_waits_for_the_rest(tracker):
    """The hook appends while Skald reads; half a line is not an event."""
    T = 1_800_000_000
    path = tracker.dirs["events"] / f"{WORLD}.log"
    whole = "\n".join(join(T, ALFR))
    path.write_text(whole[: len(whole) - 10])       # cut mid-line, no newline
    app.ingest_events()
    first = count(app.db())
    path.write_text(whole + "\n")                   # the line completes
    app.ingest_events()
    assert count(app.db()) == first + 1


def test_a_file_that_shrank_is_read_again(tracker):
    """Rotated, or replaced by a restore: re-read it, the key makes repeats free."""
    T = 1_800_000_000
    path = tracker.dirs["events"] / f"{WORLD}.log"
    path.write_text("\n".join(join(T, ALFR) + leave(T + 60, ALFR)) + "\n")
    app.ingest_events()
    assert count(app.db()) == 3
    path.write_text("\n".join(join(T, ALFR)) + "\n")  # shorter than before
    app.ingest_events()
    assert count(app.db()) == 3  # the same three lines, not six


def test_the_same_line_from_two_files_counts_once(tracker):
    """A backfill from `docker logs` overlaps the live file by design."""
    T = 1_800_000_000
    text = "\n".join(join(T, ALFR)) + "\n"
    (tracker.dirs["events"] / f"{WORLD}.log").write_text(text)
    (tracker.dirs["events"] / f"{WORLD}.backfill.log").write_text(text)
    app.ingest_events()
    assert count(app.db()) == 2


def test_milestones_json_is_imported_once(tmp_path):
    """Upgrades keep their history: the backups that dated it may be gone."""
    (tmp_path / "milestones.json").write_text(json.dumps({
        "Midgard": {
            "last": "worlds-20260101-000000.zip", "last_ts": 100.0,
            "save": {"last": "_main.9", "last_ts": 200.0, "world_time": 3000.0,
                     "keys": ["defeated_eikthyr"]},
            "live": {"defeated_gdking": {"after": 150.0, "by": 200.0}},
            "milestones": {"defeated_eikthyr": {"after": None, "by": 100.0}},
        }}))
    conn = store.connect(str(tmp_path))
    assert store.import_legacy(conn, str(tmp_path)) == 2
    assert store.import_legacy(conn, str(tmp_path)) == 0  # only ever once

    state = store.state(conn)["Midgard"]
    assert state["save"] == {"last": "_main.9", "last_ts": 200.0,
                             "world_time": 3000.0, "keys": ["defeated_eikthyr"]}
    assert state["live"]["defeated_gdking"] == {"after": 150.0, "by": 200.0}
    assert state["milestones"]["defeated_eikthyr"] == {"after": None, "by": 100.0}
    assert state["last"] == "worlds-20260101-000000.zip"


def test_nothing_to_import_is_fine(tmp_path):
    conn = store.connect(str(tmp_path))
    assert store.import_legacy(conn, str(tmp_path)) == 0


def test_history_survives_a_restart(tracker, tmp_path):
    """The point of the database: the files could vanish and the past stays."""
    T = 1_800_000_000
    tracker.write_events(join(T, ALFR), leave(T + 3600, ALFR))
    (tracker.dirs["events"] / f"{WORLD}.log").unlink()
    app._CACHE["history"] = None
    h = app.history()
    assert len(h["sessions"]) == 1
    assert h["sessions"][0]["end"] == T + 3600


def test_a_marker_skald_wrote_itself_is_kept(tracker):
    T = 1_800_000_000
    store.add_event(app.db(), WORLD, stamp(T) + ": [skald] no players online",
                    T, 3, "gone", ())
    store.add_event(app.db(), WORLD, stamp(T) + ": [skald] no players online",
                    T, 3, "gone", ())
    assert count(app.db()) == 1
