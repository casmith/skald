"""Your own numbers.

The page is meant to be the dashboard's tables narrowed to you, not a second
set of arithmetic -- so most of what is worth testing is that it agrees with
the public page, and that it narrows to the right sessions.
"""
from tests.conftest import ALFR, BERA, STEAM, join, line

from skald import app, store

T = 1_800_000_000
HOUR = 3600
NOW = T + 40 * HOUR


def user(steam_id=STEAM[ALFR]):
    store.put_user(app.db(), steam_id, {"display_name": "someone"}, T)
    return {"steam_id": steam_id, "display_name": "someone", "avatar": "",
            "character": None}


def played(ts, player, hours, character=None):
    character = character or player
    return [line(ts, f"Got connection SteamID {STEAM[player]}"),
            line(ts + 20, f"Got character ZDOID from {character} : 5:1"),
            line(ts + hours * HOUR, f"Closing socket {STEAM[player]}")]


def stats_for(tracker, *groups, steam=STEAM[ALFR]):
    h = tracker.write_events(*groups)
    u = user(steam)
    mine = app.sync_user(app.db(), u, h, NOW)
    return app.me_stats(h, NOW, [c["name"] for c in mine], u["character"]), h, u


def test_your_hours_are_the_dashboards_hours(tracker):
    """One definition of an hour played, not two."""
    st, h, u = stats_for(tracker, played(T, ALFR, 3), played(T + 10 * HOUR, BERA, 9))
    public = {r["player"]: r for r in app.playtime(h, NOW)}
    assert st["all"] == public[ALFR]["all"] == 3 * HOUR
    assert st["character"] == ALFR


def test_only_your_sessions_and_deaths(tracker):
    st, _, _ = stats_for(
        tracker,
        played(T, ALFR, 2),
        [line(T + HOUR, f"Got character ZDOID from {ALFR} : 0:0"),
         line(T + HOUR + 10, f"Got character ZDOID from {ALFR} : 7:2")],
        played(T + 10 * HOUR, BERA, 5),
        [line(T + 11 * HOUR, f"Got character ZDOID from {BERA} : 0:0")])
    assert st["deaths"] == 1                     # Bera's is not yours
    assert st["sessions"] == 1
    assert all(r["world"] for r in st["recent"])
    assert len(st["recent"]) == 1


def test_your_other_characters_are_counted_separately(tracker):
    """They are you, but they are not your primary -- so they get a line of
    their own rather than being folded into the headline."""
    st, _, _ = stats_for(tracker,
                         played(T, ALFR, 5, character="Sigrun"),
                         played(T + 8 * HOUR, ALFR, 2, character="Hrafn"))
    assert st["character"] == "Sigrun"
    assert st["all"] == 5 * HOUR
    assert st["other_seconds"] == 2 * HOUR


def test_where_you_stand_among_everyone(tracker):
    st, _, _ = stats_for(tracker, played(T, ALFR, 3), played(T + 10 * HOUR, BERA, 9))
    assert st["rank_hours"] == (2, 2)  # Bera played more


def test_the_longest_session(tracker):
    st, _, _ = stats_for(tracker, played(T, ALFR, 1),
                         played(T + 5 * HOUR, ALFR, 4),
                         played(T + 20 * HOUR, ALFR, 2))
    assert st["longest"]["seconds"] == 4 * HOUR
    assert st["longest"]["start"] == T + 5 * HOUR


def test_an_open_session_is_measured_to_now_not_the_wall_clock(tracker):
    st, _, _ = stats_for(tracker, join(T, ALFR))
    assert st["online"] is True
    # Measured from the connection, which is when they actually arrived.
    assert st["all"] == NOW - T


def test_no_primary_means_no_numbers_rather_than_an_error(tracker):
    h = tracker.write_events(played(T, BERA, 2))
    u = user()
    mine = app.sync_user(app.db(), u, h, NOW)
    assert mine == [] and u["character"] is None
    assert app.me_stats(h, NOW, [], None) is None


def test_the_page_shows_your_numbers(tracker):
    st, h, u = stats_for(tracker,
                         played(T, ALFR, 5, character="Sigrun"),
                         played(T + 8 * HOUR, ALFR, 2, character="Hrafn"),
                         played(T + 12 * HOUR, BERA, 9))
    page = app.render_me(u, app.my_characters(h, u["steam_id"], NOW),
                         store.characters(app.db(), u["steam_id"]), st)
    assert "5h 00m" in page                      # all time
    assert "2h 00m more as your other characters" in page
    assert "<b>2nd</b> of 3 by hours played" in page
    assert "Your last few" in page
    assert BERA not in page                      # someone else's evening
    assert STEAM[ALFR] not in page


def test_the_page_without_numbers_is_still_a_page(tracker):
    tracker.write_events(played(T, BERA, 2))
    u = user()
    page = app.render_me(u, [], store.characters(app.db(), u["steam_id"]), None)
    assert "has not seen this Steam account playing yet" in page
    assert "Your last few" not in page
