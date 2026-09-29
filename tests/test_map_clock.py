"""What time it is in the world, on the map.

The clock only runs while somebody is online -- the server stops it when the
world empties -- so this is the world's own day rather than how long ago it
was made. That is the number that means anything when you are working out
whether to sail somewhere before dark.
"""
import re

import pytest

from skald import app, weather


@pytest.fixture
def blank(monkeypatch):
    monkeypatch.setattr(app, "WORLD_MAPS", {
        "Midgard": {"edge": 2048, "seen": 1000, "explored": b"", "pins": []}})
    for name in ("WORLD_PORTALS", "WORLD_BUILT", "WORLD_BOATS", "WORLD_CORPSES"):
        monkeypatch.setattr(app, name, {})
    monkeypatch.setattr(app, "terrain_png", lambda w: None)


def _facts(**kw):
    page = app.render_map("Midgard",
                          shared={"Midgard": {"edge": 2048, "seen": 1000}},
                          seeds={"Midgard": {"seed": 1, "seed_name": "x"}}, **kw)
    return re.search(r'<p class="facts">.*?</p>', page, re.S).group(0)


def test_the_map_says_the_day_and_the_time(blank):
    day, hhmm = weather.day_and_clock(320_000)
    facts = _facts(clocks={"Midgard": 320_000})
    assert f"day <b>{day}</b>" in facts
    assert hhmm in facts


def test_it_says_which_part_of_the_day(blank):
    """Dawn, day, dusk or night -- the thing you actually want to know."""
    seen = set()
    for t in (100, 400, 800, 1500, 1750):
        facts = _facts(clocks={"Midgard": t})
        m = re.search(r'<span class="phase (\w+)">', facts)
        assert m, facts
        seen.add(m.group(1))
    assert seen <= {"dawn", "day", "dusk", "night"}
    assert len(seen) > 1, "the phase never changes, so it is not being read"


def test_a_world_whose_clock_is_unknown_just_says_nothing(blank):
    """A world nobody has played has no clock, and inventing one would be
    worse than leaving it out."""
    facts = _facts(clocks={"Midgard": None})
    assert "day <b>" not in facts
    assert "of the map seen" in facts


def test_no_clocks_at_all_is_not_an_error(blank):
    assert "of the map seen" in _facts()
