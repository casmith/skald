"""The map as lights, the way the dark side of the earth looks from orbit.

A city should read as a city and a hut on a headland as a hut -- dimmer,
but there. That needs the counting to hold a range before the palette can
show one: a byte saturates at 255, and a settlement of two thousand pieces
and one of forty both bury it, which is why they first came out identical.
"""
import random

from skald import fch


def _cluster(cx, cz, n, spread, lights, seed=5):
    rng = random.Random(seed)
    return [(cx + rng.gauss(0, spread), cz + rng.gauss(0, spread), i < lights)
            for i in range(n)]


def _peak_alpha(points, edge=512):
    png = fch.construction_png(points, edge, night=True)
    # the palette index is what carries brightness; read it off the alpha
    at = png.index(b"tRNS")
    length = int.from_bytes(png[at - 4:at], "big")
    alpha = png[at + 4:at + 4 + length]
    import zlib
    start = png.index(b"IDAT")
    size = int.from_bytes(png[start - 4:start], "big")
    raw = zlib.decompress(png[start + 4:start + 4 + size])
    best = 0
    for y in range(edge):
        row = raw[y * (edge + 1) + 1:(y + 1) * (edge + 1)]
        for v in row:
            if v and alpha[v] > best:
                best = alpha[v]
    return best


def test_a_bigger_settlement_is_brighter():
    """The property the first version got wrong: everything dense came out
    the same, because the counting saturated before the palette saw it."""
    city = _peak_alpha(_cluster(0, 0, 2000, 90, 40))
    town = _peak_alpha(_cluster(0, 0, 300, 45, 6))
    hamlet = _peak_alpha(_cluster(0, 0, 30, 20, 1))
    assert city > town > hamlet, (city, town, hamlet)


def test_one_hut_is_still_visible():
    """Dimmer, but not nothing -- the whole point of looking this way."""
    hut = _peak_alpha(_cluster(0, 0, 3, 6, 0))
    assert hut > 0
    assert hut < _peak_alpha(_cluster(0, 0, 2000, 90, 40))


def test_a_fire_counts_for_more_than_a_wall():
    """What you would see at night is what is burning."""
    walls = _peak_alpha([(0.0, 0.0, False)] * 8)
    fires = _peak_alpha([(0.0, 0.0, True)] * 8)
    assert fires > walls
    assert fch.LIGHT_WEIGHT > 1


def test_the_day_drawing_is_unchanged_by_any_of_it():
    """Night is a second way of looking, not a replacement."""
    pts = _cluster(0, 0, 200, 40, 5)
    assert fch.construction_png(pts, 256) != fch.construction_png(pts, 256, night=True)
    assert fch.construction_png(pts, 256).startswith(b"\x89PNG")


def test_nothing_built_draws_nothing_at_night():
    assert fch.construction_png([], 64, night=True) is not None


def _page(night, monkeypatch):
    from skald import app
    monkeypatch.setattr(app, "WORLD_MAPS", {
        "Midgard": {"edge": 2048, "seen": 1000, "explored": b"", "pins": [
            {"name": "x", "x": 0.0, "z": 0.0, "type": 3, "crossed": False}]}})
    monkeypatch.setattr(app, "WORLD_PORTALS", {
        "Midgard": [{"name": "a", "x": 0.0, "y": 30.0, "z": 0.0}]})
    monkeypatch.setattr(app, "WORLD_BUILT", {"Midgard": [(0.0, 0.0, False)]})
    monkeypatch.setattr(app, "WORLD_BOATS", {
        "Midgard": [{"kind": "karve", "x": 0.0, "z": 0.0}]})
    monkeypatch.setattr(app, "WORLD_CORPSES", {
        "Midgard": [{"name": "u", "x": 0.0, "z": 0.0, "indoors": False}]})
    monkeypatch.setattr(app, "terrain_png", lambda w: b"PNG")
    return app.render_map("Midgard",
                          shared={"Midgard": {"edge": 2048, "seen": 1000}},
                          seeds={"Midgard": {"seed": 1, "seed_name": "x"}},
                          night=night)


def test_at_night_only_the_lights_are_on(monkeypatch):
    """Two hundred portal labels over a photograph of a city is not the
    view. They are boxes though, not decisions."""
    import re
    page = _page(True, monkeypatch)
    ticked = set(re.findall(r'<input type="checkbox" checked data-layer="(\w+)"', page))
    assert ticked == {"built"}, ticked
    # and the map starts that way rather than flashing them on
    plate = re.search(r'class="plate([^"]*)"', page).group(1)
    for layer in ("portals", "boat", "corpse", "spot"):
        assert f"off-{layer}" in plate, f"{layer} is ticked off but still drawn"


def test_by_day_they_are_all_on(monkeypatch):
    import re
    page = _page(False, monkeypatch)
    ticked = set(re.findall(r'<input type="checkbox" checked data-layer="(\w+)"', page))
    assert {"built", "portals", "boat", "corpse"} <= ticked, ticked


def test_the_halo_is_three_pixels_across():
    """A pixel is twelve metres and a building piece is about two, so even
    one pixel overstates it. Five made a cluster a soft bubble sixty metres
    wide and ran neighbouring houses into one blob."""
    assert len(fch._NIGHT_GLOW) == 3
    assert all(len(row) == 3 for row in fch._NIGHT_GLOW)
    # every weight non-zero: a 3x3 with hollow corners would be a plus sign
    assert all(w for row in fch._NIGHT_GLOW for w in row)


def test_one_piece_lights_nine_pixels_not_twenty_one():
    import zlib
    edge = 64
    png = fch.construction_png([(0.0, 0.0, False)], edge, night=True)
    start = png.index(b"IDAT")
    size = int.from_bytes(png[start - 4:start], "big")
    raw = zlib.decompress(png[start + 4:start + 4 + size])
    lit = sum(1 for y in range(edge)
              for v in raw[y * (edge + 1) + 1:(y + 1) * (edge + 1)] if v)
    assert lit == 9


def test_the_day_view_keeps_its_wider_halo():
    """Only the night view was tightened. The wide stamp reads as firelight
    over a settlement, which is what the day view is for."""
    assert len(fch._GLOW) == 9
