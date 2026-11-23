"""The topographic view: the same ground, drawn for its shape.

Heights tinted, the relief shaded from the north-west, a contour every
twenty metres and a heavier one every hundred. A second drawing beside the
biome map, made by the same worker and never inside a request.
"""
import re
import struct
from array import array

from skald import app
from skald import worldgen as w


def _rows(hs, size, marsh=None):
    raw = w.topo_rows(array("f", hs), marsh or bytearray(size * size), size)
    stride = 1 + 3 * size
    return [[tuple(raw[j * stride + 1 + 3 * i:j * stride + 4 + 3 * i])
             for i in range(size)] for j in range(size)]


def _slope(size, start, per_pixel):
    """Ground rising eastward, the same in every row."""
    return [start + i * per_pixel for _ in range(size) for i in range(size)]


def test_it_draws_a_png_of_the_right_size():
    png = w.render_topo(w.World(849384111), size=24)
    assert png.startswith(b"\x89PNG")
    assert struct.unpack(">IIBB", png[16:26]) == (24, 24, 8, 2)


def test_a_contour_where_the_ground_crosses_one():
    """Rising four metres a pixel from the shore: a line at 20, 40, 60 and
    80 metres, and nowhere between them."""
    size = 32
    row = _rows(_slope(size, w.SEA_LEVEL + 1.0, 4.0), size)[size // 2]
    plain = row[2]
    lines = [i for i in range(1, size - 2)
             if sum(row[i]) < sum(row[i - 1]) and sum(row[i]) < sum(row[i + 1])]
    # 1 + 4i metres above the sea crosses a multiple of 20 between i=4,5 etc.
    assert lines[:4] == [4, 9, 14, 19], lines
    assert sum(plain) > sum(row[4])


def test_every_hundred_metres_the_line_is_heavier():
    size = 40
    row = _rows(_slope(size, w.SEA_LEVEL + 1.0, 4.0), size)[size // 2]
    light, heavy = row[19], row[24]       # 80 m and 100 m
    # compare against the fill beside each, since the tint changes with height
    assert sum(heavy) / sum(row[23]) < sum(light) / sum(row[18])


def test_the_coast_is_a_line_on_the_land_side_whichever_way_it_faces():
    """The first version only looked east and south, so every shore with
    the sea to its west or north went undrawn."""
    size = 16
    for sea_west in (True, False):
        hs = [w.SEA_LEVEL + (-3.0 if (i < 8) == sea_west else 3.0)
              for _ in range(size) for i in range(size)]
        row = _rows(hs, size)[8]
        land, water = (8, 7) if sea_west else (7, 8)
        assert row[land] == w.COAST, sea_west
        assert row[water] != w.COAST
    # and to the north and south, the same
    for sea_north in (True, False):
        hs = [w.SEA_LEVEL + (-3.0 if (j < 8) == sea_north else 3.0)
              for j in range(size) for _ in range(size)]
        rows = _rows(hs, size)
        land, water = (8, 7) if sea_north else (7, 8)
        assert rows[land][4] == w.COAST, sea_north
        assert rows[water][4] != w.COAST


def test_marsh_is_bog_not_water():
    """Marsh generates under the sea by construction, as the biome map also
    knows; painting it blue would drown every swamp."""
    size = 8
    hs = [w.SEA_LEVEL - 2.6] * (size * size)
    wet = _rows(hs, size)[4][4]
    bog = _rows(hs, size, bytearray([1] * size * size))[4][4]
    assert wet[2] > wet[0]                # blue
    assert bog != wet and bog[2] < bog[1]  # green-brown


def test_cliffs_keep_only_their_heavy_lines(monkeypatch):
    """Forty metres a pixel would run twenty-metre lines into a smear."""
    size = 32
    hs = _slope(size, w.SEA_LEVEL + 1.0, 40.0)
    # With the heavy lines spaced past the end of the slope, the twenty-metre
    # ones are all that could be drawn -- and drawing nothing at all looks
    # the same.
    monkeypatch.setattr(w, "INDEX_CONTOUR", 1e6)
    minor_only = _rows(hs, size)
    monkeypatch.setattr(w, "CONTOUR", 1e6)
    assert minor_only == _rows(hs, size)


def test_gentle_ground_keeps_its_light_lines(monkeypatch):
    size = 32
    hs = _slope(size, w.SEA_LEVEL + 1.0, 4.0)
    monkeypatch.setattr(w, "INDEX_CONTOUR", 1e6)
    minor_only = _rows(hs, size)
    monkeypatch.setattr(w, "CONTOUR", 1e6)
    assert minor_only != _rows(hs, size)


# ---- the drawing, kept ---------------------------------------------------

def _world(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app, "world_metadata",
                        lambda world: {"seed": 7, "seed_name": "x", "uid": 1})
    return "Jotunheim"


def test_serving_never_draws(monkeypatch, tmp_path):
    world = _world(monkeypatch, tmp_path)
    drew = []
    monkeypatch.setattr(app.worldgen, "render_topo",
                        lambda *a, **k: drew.append(1) or b"")
    assert app.topo_png(world) is None
    assert drew == []


def test_what_the_worker_draws_is_what_is_served(monkeypatch, tmp_path):
    world = _world(monkeypatch, tmp_path)
    monkeypatch.setattr(app.worldgen, "render_topo", lambda *a, **k: b"TOPO")
    assert app.draw_topo(world) is True
    assert app.topo_png(world) == b"TOPO"
    # and it is a different file from the map
    assert app.terrain_png(world) is None


def test_every_map_comes_before_any_topography(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    order = []
    monkeypatch.setattr(app, "SERVERS", {"A": None, "B": None})
    monkeypatch.setattr(app.worldgen, "render",
                        lambda world, *a, **k: order.append("map") or b"M")
    monkeypatch.setattr(app.worldgen, "render_topo",
                        lambda world, *a, **k: order.append("topo") or b"T")
    # both worlds share a seed here, so give them their own files
    monkeypatch.setattr(app, "world_metadata",
                        lambda world: {"seed": ord(world), "seed_name": "",
                                       "uid": 1})
    app.terrain_worker()
    assert order == ["map", "map", "topo", "topo"]


# ---- the page --------------------------------------------------------------

def _page(monkeypatch, has_topo=True, **view):
    monkeypatch.setattr(app, "WORLD_MAPS", {
        "Midgard": {"edge": 2048, "seen": 1000, "explored": b"", "pins": []}})
    monkeypatch.setattr(app, "WORLD_BUILT", {"Midgard": [(0.0, 0.0, False)]})
    monkeypatch.setattr(app, "terrain_png", lambda world: b"PNG")
    monkeypatch.setattr(app, "topo_png",
                        lambda world: b"TOPO" if has_topo else None)
    return app.render_map("Midgard",
                          shared={"Midgard": {"edge": 2048, "seen": 1000}},
                          seeds={"Midgard": {"seed": 1, "seed_name": "x"}},
                          **view)


def test_the_topographic_view_swaps_the_ground(monkeypatch):
    page = _page(monkeypatch, topo=True)
    assert 'src="/topo.png?world=Midgard"' in page
    assert "/terrain.png" not in page
    assert re.search(r'class="plate[^"]* topo', page)
    assert "leave topography" in page


def test_the_day_view_offers_it(monkeypatch):
    page = _page(monkeypatch)
    assert 'src="/terrain.png?world=Midgard"' in page
    assert 'href="/map?world=Midgard&topo=1"' in page


def test_not_offered_until_it_is_drawn(monkeypatch):
    """A link to a picture that is not coming is a broken link."""
    page = _page(monkeypatch, has_topo=False, topo=True)
    assert "topograph" not in page
    assert 'src="/terrain.png?world=Midgard"' in page


def test_one_view_at_a_time(monkeypatch):
    page = _page(monkeypatch, topo=True, night=True)
    plate = re.search(r'class="plate([^"]*)"', page).group(1)
    assert "topo" in plate and "night" not in plate
    assert 'src="/built.png?world=Midgard"' in page


def test_the_fog_link_keeps_the_view(monkeypatch):
    import dataclasses
    monkeypatch.setattr(app, "CONFIG",
                        dataclasses.replace(app.CONFIG, allow_fog_off=True))
    page = _page(monkeypatch, topo=True)
    assert 'href="/map?world=Midgard&fog=0&topo=1"' in page
