"""Boss kills and other firsts, and where their timing comes from.

A kill sets a global key the world saves. Three sources date it, best first:
the server log (exact), the live autosaves (a 30-minute window), and the
hourly backups (about 90 minutes).
"""
from tests.conftest import ALFR, BERA, WORLD, join, leave, line, world_save

from skald import app

T = 1_800_000_000
EIKTHYR, ELDER = "defeated_eikthyr", "defeated_gdking"


def test_a_key_is_read_as_its_length_byte_says(tracker):
    """A greedy match would run past the key into whatever follows it."""
    keys, world_time, version = app.db2_world(world_save([EIKTHYR, "killedtroll"], 999.5))
    assert keys == {EIKTHYR, "killedtroll"}
    assert world_time == 999.5
    assert version == 41  # the format these fixtures are written in


def test_text_that_merely_looks_like_a_key_is_ignored(tracker):
    import gzip
    import struct
    body = b"xx" + b"defeated_fake" + bytes([5]) + b"killedbat"
    blob = gzip.compress(body)
    keys, _, _ = app.db2_world(struct.pack("<idi", 41, 0.0, len(blob)) + blob)
    assert keys == set()


def test_the_first_save_seen_dates_its_keys_as_already_done(tracker):
    """A world with history still shows it: "by then", with no lower bound."""
    tracker.write_save(1, [EIKTHYR], T)
    app.scan_saves()
    state = app.load_milestones()[WORLD]
    assert state["save"]["keys"] == [EIKTHYR]
    assert state["live"] == {EIKTHYR: {"after": None, "by": T}}


def test_a_milestone_from_the_baseline_reads_as_before_that_save(tracker):
    tracker.write_save(1, [EIKTHYR], T)
    app.scan_saves()
    (m,) = app.milestones({"sessions": [], "keys": [], "spawns": []})
    assert (m["earliest"], m["latest"]) == (None, T)
    assert m["label"] == "Eikthyr defeated"


def test_a_key_new_in_a_save_is_pinned_between_two_saves(tracker):
    tracker.write_save(1, [EIKTHYR], T)
    app.scan_saves()
    tracker.write_save(2, [EIKTHYR, ELDER], T + 1800)
    app.scan_saves()
    assert app.load_milestones()[WORLD]["live"][ELDER] == {"after": T, "by": T + 1800}


def test_rescanning_without_a_new_save_changes_nothing(tracker):
    tracker.write_save(1, [EIKTHYR], T)
    app.scan_saves()
    tracker.write_save(2, [EIKTHYR, ELDER], T + 1800)
    app.scan_saves()
    before = app.load_milestones()
    app.scan_saves()
    assert app.load_milestones() == before


def test_the_log_gives_the_exact_moment_and_the_fight(tracker):
    tracker.write_save(1, [], T)
    app.scan_saves()
    tracker.write_save(2, [ELDER], T + 1800)
    app.scan_saves()
    h = tracker.write_events(
        join(T - 600, ALFR),
        [line(T + 900, "Spawning boss at (1.0, 2.0, 3.0) using spawn point (1,2,3)"),
         line(T + 1260, f"Setting global key {ELDER}")],
        leave(T + 1700, ALFR))
    (m,) = [m for m in app.milestones(h) if m["key"] == ELDER]
    assert m["source"] == "log"
    assert m["earliest"] == m["latest"] == T + 1260
    assert m["fight_seconds"] == 360
    assert m["online"] == [ALFR]


def test_a_key_relogged_after_a_restart_is_not_a_kill(tracker):
    """Only a log time inside the save window can be the kill itself."""
    tracker.write_save(1, [], T)
    app.scan_saves()
    tracker.write_save(2, [EIKTHYR], T + 1800)
    app.scan_saves()
    h = tracker.write_events([line(T + 9000, f"Setting global key {EIKTHYR}")])
    (m,) = [m for m in app.milestones(h) if m["key"] == EIKTHYR]
    assert m["source"] == "save"
    assert (m["earliest"], m["latest"]) == (T, T + 1800)


def test_keys_that_are_not_milestones_are_left_alone(tracker):
    h = tracker.write_events([line(T, "Setting global key activebosses 1"),
                              line(T + 5, "Setting global key nomap")])
    assert app.milestones(h) == []


def test_bosses_unlock_biomes(tracker):
    """A biome shows once the boss before it is down -- and not before."""
    tracker.write_save(1, [EIKTHYR], T)
    app.scan_saves()
    h = tracker.write_events(join(T, BERA))
    assert app.unlocked_biomes(h, WORLD) == ["Meadows", "Black Forest", "Ocean"]
    tracker.write_save(2, [EIKTHYR, ELDER], T + 1800)
    app.scan_saves()
    assert "Swamp" in app.unlocked_biomes(h, WORLD)
    assert "Mountain" not in app.unlocked_biomes(h, WORLD)


def test_a_save_format_skald_has_not_seen_is_read_anyway(tracker, capsys):
    """The header has not moved in a long time. Read it, and say so."""
    import gzip
    import struct

    body = b"x" + bytes([len(EIKTHYR)]) + EIKTHYR.encode()
    blob = gzip.compress(body)
    future = struct.pack("<idi", 99, 4242.0, len(blob)) + blob
    d = tracker.dirs["saves"] / WORLD / "worlds_local" / WORLD
    d.mkdir(parents=True, exist_ok=True)
    (d / "_main.5.db2").write_bytes(future)
    (d / "_main.5.ok").write_text("ok")
    app.scan_saves()

    assert "format 99" in capsys.readouterr().out
    state = app.load_milestones()[WORLD]
    assert state["save"]["version"] == 99
    assert state["save"]["world_time"] == 4242.0   # still usable
    assert EIKTHYR in state["live"]


def test_a_generated_label_has_no_double_space():
    """`killed_surtling` and `killedbat` are both real spellings, so the
    separator is optional -- and stripping it used to leave a gap."""
    assert app.milestone_label("killed_seekerbrood") == (
        "First seekerbrood killed", "other")
    assert app.milestone_label("killedabomination") == (
        "First abomination killed", "other")
    assert app.milestone_label("defeated_morgen") == ("Morgen defeated", "other")
    # A key matching neither prefix keeps its old shape: no invented "First".
    assert app.milestone_label("bosshildir4") == ("Bosshildir4 killed", "other")


def test_no_generated_label_is_blank_or_ragged():
    for key in ["defeated_", "killed_", "killed", "defeated_a_b_c", "killedx"]:
        label, _ = app.milestone_label(key)
        assert label.strip() == label
        assert "  " not in label


def test_the_keys_from_the_games_own_bundles_are_named():
    """Extracted from every Character prefab's m_defeatSetGlobalKey. Deep
    North is unfinished, so nobody can set these yet -- which is exactly why
    they are worth naming before anyone can."""
    for key in ["defeated_frozenking", "defeated_frozenking_p3", "defeated_hive",
                "killed_frysling", "defeated_serpent", "defeated_writhan",
                "killed_surtling"]:
        label, kind = app.milestone_label(key)
        assert kind != "other", f"{key} should have a hand-written label"
        assert key.split("_")[-1][:4].lower() in label.lower().replace(" ", "") \
            or key in ("defeated_serpent", "defeated_frozenking_p3")


def test_a_boss_kind_must_be_in_the_badge_row(tracker):
    """The trap: the table skips kind "boss" because the badges show those
    instead -- so a "boss" missing from BOSSES is filtered out of the table
    and absent from the badges, and disappears entirely."""
    badge_keys = {key for key, _ in app.BOSSES}
    for key, (_, kind) in app.MILESTONES.items():
        if kind == "boss":
            assert key in badge_keys, f"{key} is kind 'boss' but not in BOSSES"
