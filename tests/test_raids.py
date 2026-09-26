"""Raids.

The server logs a raid starting, exactly, and says nothing when it ends --
so a raid is a moment here, not a span. The lines below are copied from a
live server, not invented.
"""
from tests.conftest import ALFR, BERA, WORLD, join, leave, line

from skald import app

T = 1_800_000_000
NOW = T + 86400


def raid(ts, event):
    return [line(ts, f"Random event set:{event}")]


def test_the_line_a_real_server_writes(tracker):
    h = tracker.write_events(raid(T, "army_bonemass"), raid(T + 600, "foresttrolls"))
    got = app.raids(h, NOW)
    assert [r["event"] for r in got] == ["foresttrolls", "army_bonemass"]  # newest first
    assert got[1]["label"] == "Bonemass' army"
    assert got[0]["label"] == "Troll raid"


def test_clearing_the_event_is_not_a_raid(tracker):
    """`Random event set:` with nothing after it is the event ending. It
    must not be recorded as a raid starting."""
    h = tracker.write_events(raid(T, "army_bonemass"),
                             [line(T + 120, "Random event set:")])
    assert len(h["raids"]) == 1


def test_who_was_there_for_it(tracker):
    h = tracker.write_events(
        join(T, ALFR), join(T + 60, BERA),
        raid(T + 600, "wolves"),
        leave(T + 1200, BERA), leave(T + 1800, ALFR),
        raid(T + 3600, "skeletons"))          # after everyone left
    by_event = {r["event"]: r for r in app.raids(h, NOW)}
    assert by_event["wolves"]["online"] == [ALFR, BERA]
    assert by_event["skeletons"]["online"] == []


def test_a_raid_during_an_open_session_counts_the_player(tracker):
    h = tracker.write_events(join(T, ALFR), raid(T + 600, "blobs"))
    assert app.raids(h, NOW)[0]["online"] == [ALFR]


def test_an_unknown_raid_still_reads_as_something(tracker):
    """Valheim has added raids biome by biome and will again."""
    h = tracker.write_events(raid(T, "army_seeker"), raid(T + 60, "gjallhorde"))
    got = {r["event"]: r["label"] for r in app.raids(h, NOW)}
    assert got["army_seeker"] == "Seeker army"
    assert got["gjallhorde"] == "Gjallhorde raid"


def test_every_known_raid_has_a_label():
    for event, label in app.RAIDS.items():
        assert label and label[0].isupper(), event
    # The ten the game's own _GameMain list references: the core set.
    for core in ("army_eikthyr", "army_theelder", "army_bonemass", "army_moder",
                 "army_goblin", "foresttrolls", "skeletons", "blobs", "wolves",
                 "surtlings"):
        assert core in app.RAIDS


def test_raids_are_per_world(tracker):
    tracker.write_events(raid(T, "wolves"))
    h = tracker.write_events(raid(T + 60, "skeletons"), world="Other")
    assert [r["event"] for r in app.raids(app.for_world(h, "Other"), NOW)] == ["skeletons"]
    assert [r["event"] for r in app.raids(app.for_world(h, WORLD), NOW)] == ["wolves"]


def test_the_page_lists_them(tracker):
    import html as html_mod
    h = tracker.write_events(join(T, ALFR), raid(T + 600, "army_moder"))
    page = app.render(h, NOW, WORLD)
    # The label is escaped on the way out, apostrophe and all, like every
    # other label on the page.
    assert "Raids" in page
    assert html_mod.escape("Moder's army") in page
    assert ALFR in page
