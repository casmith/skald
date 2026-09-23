"""The world clock, and what the page says.

Valheim's world clock only advances while someone is online: the server
stops it when a world empties. Weather and the in-game day both hang off
that, so it is worth pinning.
"""
from tests.conftest import ALFR, BERA, WORLD, join, leave, stamp

from skald import app, config

T = 1_800_000_000


def test_the_clock_advances_only_while_someone_is_online(tracker):
    tracker.write_save(1, ["defeated_eikthyr"], T, world_time=1000.0)
    app.scan_saves()
    h = tracker.write_events(join(T + 600, ALFR), leave(T + 1200, ALFR))
    # An hour of wall time, ten minutes of it played.
    assert app.world_clock(h, WORLD, T + 3600) == 1000.0 + 600


def test_overlapping_players_do_not_double_count_the_clock(tracker):
    tracker.write_save(1, [], T, world_time=0.0)
    app.scan_saves()
    h = tracker.write_events(join(T, ALFR), join(T + 60, BERA),
                             leave(T + 600, ALFR), leave(T + 900, BERA))
    assert app.world_clock(h, WORLD, T + 5000) == 900


def test_without_a_save_the_clock_is_unknown(tracker):
    h = tracker.write_events(join(T, ALFR))
    assert app.world_clock(h, WORLD, T + 100) is None
    assert app.weather_report(h, WORLD, T + 100) is None


def test_the_page_renders_the_sections(tracker):
    tracker.write_save(1, ["defeated_eikthyr"], T, world_time=50_000.0)
    app.scan_saves()
    h = tracker.write_events(join(T, ALFR), leave(T + 3600, ALFR))
    page = app.render(h, T + 4000, WORLD)
    for section in ("Weather", "Bosses slain", "Playtime", "Deaths",
                    "Recent sessions", "7-day forecast"):
        assert section in page
    assert ALFR in page and WORLD in page


def test_the_page_names_neither_unbeaten_bosses_nor_locked_biomes(tracker):
    """No spoilers: what a world has not reached is not in the HTML at all."""
    tracker.write_save(1, ["defeated_eikthyr"], T, world_time=50_000.0)
    app.scan_saves()
    h = tracker.write_events(join(T, ALFR))
    page = app.render(h, T + 100, WORLD)
    for name in ("Bonemass", "Moder", "Yagluth", "The Queen", "Fader"):
        assert name not in page
    for biome in ("Swamp", "Mountain", "Plains", "Mistlands", "Ashlands", "Deep North"):
        assert biome not in page
    assert "Eikthyr" in page and "Black Forest" in page


def test_an_unknown_world_is_not_served(tracker):
    h = tracker.write_events(join(T, ALFR))
    assert app.pick_world(h, "nowhere") is None
    assert app.pick_world(h, "testheim") == WORLD  # case does not matter
    assert app.pick_world(h, "") == WORLD          # the default


def test_the_api_shapes(tracker):
    tracker.write_save(1, ["defeated_eikthyr"], T, world_time=50_000.0)
    app.scan_saves()
    h = tracker.write_events(join(T, ALFR), leave(T + 60, ALFR))
    now = T + 600
    assert app.playtime(h, now)[0]["player"] == ALFR
    assert app.recent(h, now, 10)[0]["seconds"] == 60
    assert len(app.daily(h, now, 3)) == 3
    assert app.weather_report(h, WORLD, now)["biomes"][0]["biome"] == "Meadows"


def test_a_crash_closes_the_session_at_the_last_sighting(tracker):
    """No Closing socket ever arrives; the poller leaves a marker instead,
    dated to the last time anyone was seen, and that ends the session."""
    h = tracker.write_events(join(T, ALFR))
    assert h["sessions"][0]["end"] is None
    marker = tracker.dirs["data"] / f"{WORLD}.reconcile.log"
    marker.write_text(stamp(T + 1800) + ": [tracker] no players online\n")
    app._CACHE.update(sig=None, history=None)
    assert app.history()["sessions"][0]["end"] == T + 1800


def test_diagnostics_reports_what_it_can_see(tracker):
    tracker.write_save(1, ["defeated_eikthyr"], T, world_time=50_000.0)
    app.scan_saves()
    h = tracker.write_events(join(T, ALFR), leave(T + 60, ALFR))
    d = app.diagnostics(h, T + 600)
    assert d["paths"]["events"]["exists"] and d["paths"]["events"]["writable"]
    (w,) = d["worlds"]
    assert w["world"] == WORLD
    assert w["events_file"]["exists"] and w["events_file"]["sessions_seen"] == 1
    assert w["saves"]["latest"] == "_main.1"
    assert w["saves"]["world_clock"] == 50_060  # the save, plus a minute played
    assert w["backups"]["count"] == 0
    assert w["status"] == "not polled yet"
    assert "Diagnostics" in app.render_diagnostics(d)


def test_diagnostics_says_when_a_path_is_missing(tracker):
    cfg = app.CONFIG
    app.apply_config(config.replace(cfg, saves_root="/nowhere-at-all"))
    h = tracker.write_events(join(T, ALFR))
    d = app.diagnostics(h, T + 60)
    assert d["paths"]["saves_root"]["exists"] is False
    assert d["worlds"][0]["saves"]["exists"] is False
