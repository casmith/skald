"""Which characters are yours, and which one is you.

The point of these tests is that nothing here is a free-form claim. The log
pairs a connection's SteamID with the character that followed it, so Skald
already knows who played what; the rows only record what to do with that.
So the cases worth writing are the ones where someone reaches for a
character that is not theirs, and the ones where the automatic choice has to
stop moving.
"""
from tests.conftest import ALFR, BERA, STEAM, WORLD, line

from skald import app, store

T = 1_800_000_000
HOUR = 3600
NOW = T + 100 * HOUR


def user(steam_id=STEAM[ALFR], name="alfr-on-steam"):
    store.put_user(app.db(), steam_id, {"display_name": name}, T)
    return {"steam_id": steam_id, "display_name": name, "avatar": "",
            "character": None}


def played(ts, player, hours, character=None):
    """A session of `hours`, optionally under a second character's name."""
    if character is None:
        character = player
    return [line(ts, f"Got connection SteamID {STEAM[player]}"),
            line(ts + 20, f"Got character ZDOID from {character} : 5:1"),
            line(ts + hours * HOUR, f"Closing socket {STEAM[player]}")]


def names(chars):
    return [c["name"] for c in chars]


def test_only_the_characters_that_account_has_played(tracker):
    h = tracker.write_events(played(T, ALFR, 2), played(T + 5 * HOUR, BERA, 3))
    assert names(app.my_characters(h, STEAM[ALFR], NOW)) == [ALFR]
    assert names(app.my_characters(h, STEAM[BERA], NOW)) == [BERA]
    # Someone who has never played has nothing to take up.
    assert app.my_characters(h, "76561190000009999", NOW) == []


def test_several_characters_most_played_first(tracker):
    h = tracker.write_events(
        played(T, ALFR, 1, character="Sigrun"),
        played(T + 3 * HOUR, ALFR, 4, character="Hrafn"))
    assert names(app.my_characters(h, STEAM[ALFR], NOW)) == ["Hrafn", "Sigrun"]


def test_a_session_with_no_steam_id_still_counts_as_playtime(tracker):
    """A backfilled log can start mid-connection, so some sessions have no
    SteamID. That must not halve the playtime of a character we have
    otherwise identified -- it is the same character either way."""
    h = tracker.write_events(
        played(T, ALFR, 1),
        # No connection line for this one, so the session carries no SteamID;
        # the server shutting down is what ends it.
        [line(T + 5 * HOUR, f"Got character ZDOID from {ALFR} : 9:1"),
         line(T + 7 * HOUR, "Game - OnApplicationQuit")])
    mine = app.my_characters(h, STEAM[ALFR], T + 8 * HOUR)
    assert names(mine) == [ALFR]
    assert mine[0]["sessions"] == 2
    assert mine[0]["seconds"] == 3 * HOUR


def test_the_most_played_character_becomes_primary_by_itself(tracker):
    h = tracker.write_events(
        played(T, ALFR, 1, character="Sigrun"),
        played(T + 3 * HOUR, ALFR, 4, character="Hrafn"))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Hrafn"
    assert store.session_user(app.db(), *_session(u)) ["character"] == "Hrafn"


def test_the_automatic_choice_follows_the_playtime(tracker):
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"))
    u = user()
    assert app.sync_user(app.db(), u, h, NOW) and u["character"] == "Sigrun"
    # A long evening as the other one, and Skald follows.
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"),
                             played(T + 20 * HOUR, ALFR, 9, character="Hrafn"))
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Hrafn"


def test_choosing_one_by_hand_stops_it_moving(tracker):
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    assert store.set_primary(app.db(), u["steam_id"], "Hrafn")
    # Sigrun is still the most played, but the choice was explicit.
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Hrafn"
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"),
                             played(T + 30 * HOUR, ALFR, 20, character="Sigrun"))
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Hrafn"


def test_a_character_you_have_not_played_cannot_be_made_primary(tracker):
    """The whole security of this on a public instance: there is no row for
    a character this account never played, so there is nothing to point at."""
    h = tracker.write_events(played(T, ALFR, 2), played(T + 5 * HOUR, BERA, 3))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    assert store.set_primary(app.db(), u["steam_id"], BERA) is False
    assert u["character"] == ALFR
    assert names(store.characters(app.db(), u["steam_id"])) == [ALFR]


def test_hiding_a_character_picks_another_and_does_not_take_it_back(tracker):
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Sigrun"
    store.hide_character(app.db(), u["steam_id"], "Sigrun")
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Hrafn"
    # The log still says Sigrun is theirs; the tombstone is what keeps it off.
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Hrafn"
    rows = {r["name"]: r for r in store.characters(app.db(), u["steam_id"])}
    assert rows["Sigrun"]["hidden"] == 1


def test_taking_a_hidden_character_back(tracker):
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    store.hide_character(app.db(), u["steam_id"], "Sigrun")
    app.sync_user(app.db(), u, h, NOW)
    store.hide_character(app.db(), u["steam_id"], "Sigrun", hidden=False)
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] == "Sigrun"  # most played again, and no longer hidden


def test_hiding_every_character_leaves_no_primary(tracker):
    h = tracker.write_events(played(T, ALFR, 2))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    store.hide_character(app.db(), u["steam_id"], ALFR)
    app.sync_user(app.db(), u, h, NOW)
    assert u["character"] is None


def test_at_most_one_primary_ever(tracker):
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    store.set_primary(app.db(), u["steam_id"], "Hrafn")
    store.set_primary(app.db(), u["steam_id"], "Sigrun")
    primaries = [r["name"] for r in store.characters(app.db(), u["steam_id"])
                 if r["is_primary"]]
    assert primaries == ["Sigrun"]


def test_two_accounts_keep_their_own_choices(tracker):
    h = tracker.write_events(played(T, ALFR, 2), played(T + 5 * HOUR, BERA, 3))
    a, b = user(), user(STEAM[BERA], "bera-on-steam")
    app.sync_user(app.db(), a, h, NOW)
    app.sync_user(app.db(), b, h, NOW)
    assert (a["character"], b["character"]) == (ALFR, BERA)


def test_the_page_names_your_characters_and_nobody_elses(tracker):
    h = tracker.write_events(
        played(T, ALFR, 4, character="Sigrun"),
        played(T + 6 * HOUR, ALFR, 1, character="Hrafn"),
        played(T + 12 * HOUR, BERA, 2, character="Ylva"))
    u = user()
    mine = app.sync_user(app.db(), u, h, NOW)
    page = app.render_me(u, mine, store.characters(app.db(), u["steam_id"]))
    assert "Sigrun" in page and "Hrafn" in page
    assert "Ylva" not in page
    assert "most-played character" in page
    # Never the Steam ID, on this page least of all.
    assert STEAM[ALFR] not in page


def test_the_page_says_when_a_choice_was_made_by_hand(tracker):
    h = tracker.write_events(played(T, ALFR, 4, character="Sigrun"),
                             played(T + 6 * HOUR, ALFR, 1, character="Hrafn"))
    u = user()
    mine = app.sync_user(app.db(), u, h, NOW)
    store.set_primary(app.db(), u["steam_id"], "Hrafn")
    page = app.render_me(u, mine, store.characters(app.db(), u["steam_id"]))
    assert "because you said so" in page


def test_the_page_holds_up_with_nothing_to_show(tracker):
    h = tracker.write_events(played(T, BERA, 2))
    u = user()
    mine = app.sync_user(app.db(), u, h, NOW)
    page = app.render_me(u, mine, store.characters(app.db(), u["steam_id"]))
    assert "has not seen this Steam account playing yet" in page


def test_the_dashboard_calls_you_by_your_character(tracker):
    h = tracker.write_events(played(T, ALFR, 2, character="Sigrun"))
    u = user()
    app.sync_user(app.db(), u, h, NOW)
    app.CONFIG = app.CONFIG.__class__(**{**app.CONFIG.__dict__,
                                         "base_url": "https://skald.example.com"})
    page = app.render(h, T + HOUR, WORLD, u)
    assert "signed in as" in page and "Sigrun" in page
    assert "your characters" in page


def _session(u):
    """Start a real session for this user and return what looks it up."""
    token = "token-for-" + u["steam_id"]
    store.start_session(app.db(), token, u["steam_id"], T, T + 86400)
    return token, T + 1
