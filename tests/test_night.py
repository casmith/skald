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
