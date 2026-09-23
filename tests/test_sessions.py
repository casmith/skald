"""Sessions, deaths and exploration, rebuilt from the server log.

The log is all skald has: Valheim's Steam query reports a player count but
blanks every name. These tests pin the awkward cases that cost real debugging
-- deaths that look like joins, respawns that look like second sessions, and
drops that are never closed.
"""
from tests.conftest import ALFR, BERA, WORLD, died, join, leave, line, respawned

T = 1_800_000_000


def only(sessions, player):
    return [s for s in sessions if s["player"] == player]


def test_a_join_and_a_leave_make_one_session(tracker):
    h = tracker.write_events(join(T, ALFR), leave(T + 3600, ALFR))
    (s,) = h["sessions"]
    assert (s["player"], s["world"]) == (ALFR, WORLD)
    # The session starts at the connection, not at the character line.
    assert (s["start"], s["end"]) == (T, T + 3600)


def test_a_session_is_open_until_the_socket_closes(tracker):
    h = tracker.write_events(join(T, ALFR))
    assert h["sessions"][0]["end"] is None


def test_zdoid_zero_is_a_death_not_a_join(tracker):
    """`Got character ZDOID from X : 0:0` is how Valheim logs a death."""
    h = tracker.write_events(join(T, ALFR), died(T + 600, ALFR),
                             respawned(T + 608, ALFR), leave(T + 1200, ALFR))
    assert len(h["sessions"]) == 1
    assert [(d["player"], d["ts"]) for d in h["deaths"]] == [(ALFR, T + 600)]


def test_a_quick_reconnect_is_one_session(tracker):
    """A drop and a rejoin inside MERGE_GAP is one session, not two."""
    h = tracker.write_events(join(T, ALFR), leave(T + 1000, ALFR),
                             join(T + 1030, ALFR), leave(T + 2000, ALFR))
    (s,) = only(h["sessions"], ALFR)
    assert (s["start"], s["end"]) == (T, T + 2000)


def test_a_long_gap_is_two_sessions(tracker):
    gap = tracker.module.MERGE_GAP + 60
    h = tracker.write_events(join(T, ALFR), leave(T + 1000, ALFR),
                             join(T + 1000 + gap, ALFR), leave(T + 5000, ALFR))
    assert len(only(h["sessions"], ALFR)) == 2


def test_a_second_connection_closes_the_first(tracker):
    """A reconnect whose Closing socket never arrived must not leave two open."""
    h = tracker.write_events(join(T, ALFR), join(T + 4000, ALFR))
    ended, still_open = sorted(h["sessions"], key=lambda s: s["start"])
    assert ended["end"] == T + 4000 and still_open["end"] is None


def test_the_server_shutting_down_ends_every_session(tracker):
    h = tracker.write_events(join(T, ALFR), join(T + 30, BERA),
                             [line(T + 900, "Game - OnApplicationQuit")])
    assert all(s["end"] == T + 900 for s in h["sessions"])


def test_players_are_tracked_separately(tracker):
    h = tracker.write_events(join(T, ALFR), join(T + 60, BERA),
                             leave(T + 600, ALFR), died(T + 700, BERA))
    assert {s["player"] for s in h["sessions"]} == {ALFR, BERA}
    assert [d["player"] for d in h["deaths"]] == [BERA]


def test_a_zone_is_counted_once_however_many_landmarks_it_holds(tracker):
    h = tracker.write_events(
        join(T, ALFR),
        [line(T + 10, "Placed location Crypt2 in zone 3,-4  duration 4.0 ms"),
         line(T + 11, "Placed location Ruin1 in zone 3,-4  duration 2.0 ms"),
         line(T + 12, "Placed location TrollCave02 in zone 9,9  duration 1.0 ms")])
    assert [e["zone"] for e in h["explored"]] == ["3,-4", "9,9"]


def test_playtime_and_deaths_add_up(tracker):
    now = T + 7200
    h = tracker.write_events(join(T, ALFR), died(T + 100, ALFR),
                             died(T + 200, ALFR), leave(T + 3600, ALFR))
    (row,) = tracker.module.playtime(h, now)
    assert row["player"] == ALFR
    assert row["all"] == 3600
    assert row["deaths"]["all"] == 2


def test_history_is_rebuilt_only_when_the_files_change(tracker):
    first = tracker.write_events(join(T, ALFR))
    assert tracker.module.history() is first  # cached
    second = tracker.write_events(join(T, ALFR), leave(T + 60, ALFR))
    assert second is not first
    assert second["sessions"][0]["end"] == T + 60
