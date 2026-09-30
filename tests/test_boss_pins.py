"""Which boss, not just that there is one.

Every boss pin used to be the same skull, with the name reachable only by
hovering. On a map you are reading to decide where to go next, which altar
is the whole question -- and a tooltip answers it one pin at a time.
"""
import re

import pytest

from skald import app


def _page(pins, monkeypatch):
    monkeypatch.setattr(app, "WORLD_MAPS", {
        "Midgard": {"edge": 2048, "seen": 1000, "explored": b"",
                    "pins": pins}})
    for name in ("WORLD_PORTALS", "WORLD_BUILT", "WORLD_BOATS",
                 "WORLD_CORPSES"):
        monkeypatch.setattr(app, name, {"Midgard": []})
    monkeypatch.setattr(app, "terrain_png", lambda w: b"PNG")
    return app.render_map("Midgard",
                          shared={"Midgard": {"edge": 2048, "seen": 1000}},
                          seeds={"Midgard": {"seed": 1, "seed_name": "x"}})


def _boss(token, x=0.0, z=0.0, crossed=False):
    return {"name": token, "x": x, "z": z, "type": 9, "crossed": crossed}


def _marks(page):
    # Only the markers the server drew. The inline script carries a template
    # for the portal markers it builds in the browser, and that looks enough
    # like a marker to match a looser pattern.
    box = re.search(r'<div class="pins">(.*?)</div>', page)
    assert box, "the page drew no pins at all"
    return re.findall(r"<b class=\"pin .*?</b>", box.group(1))


@pytest.mark.parametrize("token,name", sorted(app.BOSS_NAMES.items()))
def test_each_boss_is_named_on_the_map(token, name, monkeypatch):
    page = _page([_boss(token)], monkeypatch)
    mark, = _marks(page)
    assert app.BOSS_GLYPHS[name] in mark
    # in the markup, not tucked into a title= nobody will hover
    assert f"<i>{name}</i>" in mark
    assert "title=" not in mark


def test_no_two_bosses_share_a_mark():
    """The point of the change: seven skulls told you nothing."""
    glyphs = list(app.BOSS_GLYPHS.values())
    assert len(set(glyphs)) == len(glyphs)
    assert len(glyphs) == 7


def test_a_boss_the_game_adds_later_keeps_the_skull(monkeypatch):
    page = _page([_boss("$enemy_someone_new")], monkeypatch)
    mark, = _marks(page)
    assert "☠" in mark
    assert "<i>" not in mark
    # unknown, so the token is all there is -- as a tooltip, as before
    assert 'title="enemy_someone_new"' in mark


def test_an_ordinary_pin_is_left_alone(monkeypatch):
    page = _page([{"name": "camp", "x": 0.0, "z": 0.0,
                   "type": 3, "crossed": False}], monkeypatch)
    mark, = _marks(page)
    assert "<i>" not in mark
    assert 'title="camp"' in mark


def test_a_defeated_boss_is_struck_through(monkeypatch):
    """Crossing a pin off is how the game records the kill, and the name
    beside it has to show that too or it reads as still standing."""
    page = _page([_boss("$enemy_bonemass", crossed=True)], monkeypatch)
    mark, = _marks(page)
    assert "done" in mark and "<i>Bonemass</i>" in mark
    assert ".pin.boss.done i{text-decoration:line-through}" in page


def test_the_three_lists_of_bosses_agree():
    """Three places name the same seven: the tokens the game's pins carry,
    the badge row in progression order, and the marks on the map. A boss in
    one and not the others is a silent hole, and shadowing one of them with
    another is how this test came to exist."""
    badges = {name for _, name in app.BOSSES}
    assert badges == set(app.BOSS_GLYPHS)
    assert badges == set(app.BOSS_NAMES.values())
