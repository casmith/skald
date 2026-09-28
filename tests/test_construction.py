"""Where people have built, as opposed to where there is masonry.

The world's own ruins and dungeons are made from the same prefabs players
build with, and there are far more of them -- on one of these worlds fifty
thousand generated pieces against a few thousand built ones. A piece someone
placed records who placed it; a generated one does not. Without that test the
map shows every ruin in the world and the places people actually live are
lost in the scatter, which is what the first attempt at this drew.
"""
import struct

from skald import fch
from skald.worldgen import stable_hash

CREATOR = struct.pack("<i", fch.CREATOR_KEY)


def _piece(name, x, z, creator=True, y=30.0):
    out = struct.pack("<3f", x, y, z)
    out += struct.pack("<i", stable_hash(name))
    out += b"\x01\x02\x03"
    if creator:
        out += CREATOR + struct.pack("<q", 7788)
    return out


def _prefabs():
    return [struct.pack("<i", stable_hash(n)) for n in fch.BUILD_PIECES]


def test_only_what_somebody_built_is_counted(tmp_path):
    path = tmp_path / "a.chunk"
    path.write_bytes(
        _piece("wood_floor", 100.0, 200.0)
        + _piece("stone_wall_2x1", -4000.0, 3000.0, creator=False)
        + _piece("wood_beam", 104.0, 202.0))
    got = fch._construction_in(str(path), _prefabs())
    assert [(round(x), round(z)) for x, z in got] == [(100, 200), (104, 202)]


def test_a_world_of_ruins_reads_as_empty(tmp_path):
    """A world nobody has built in yet is not a world covered in stone."""
    path = tmp_path / "b.chunk"
    path.write_bytes(b"".join(
        _piece("stone_wall_2x1", 100.0 * i, 200.0, creator=False)
        for i in range(30)))
    assert fch._construction_in(str(path), _prefabs()) == []


def test_one_bad_point_does_not_lose_the_rest(tmp_path):
    """Unlike the portals, this searches for many prefabs rather than one,
    so four bytes will land by chance eventually. Throwing away a world's
    building over one of them would be the wrong trade."""
    body = bytearray(_piece("wood_floor", 50.0, 60.0))
    body += struct.pack("<3f", 9e9, 30.0, 0.0)
    body += struct.pack("<i", stable_hash("wood_beam")) + CREATOR + struct.pack("<q", 1)
    body += _piece("wood_roof", 70.0, 80.0)
    path = tmp_path / "c.chunk"
    path.write_bytes(bytes(body))
    got = fch._construction_in(str(path), _prefabs())
    assert [(round(x), round(z)) for x, z in got] == [(50, 60), (70, 80)]


def test_a_creator_far_away_belongs_to_nothing(tmp_path):
    """The creator has to be near its piece. A loose one further down the
    file is some other object's, and must not make a ruin look built."""
    body = _piece("stone_wall_2x1", 10.0, 10.0, creator=False)
    body += b"\x00" * (fch.CREATOR_WINDOW + 40) + CREATOR + struct.pack("<q", 5)
    path = tmp_path / "d.chunk"
    path.write_bytes(body)
    assert fch._construction_in(str(path), _prefabs()) == []


def test_the_drawing_is_empty_where_nobody_built():
    """Index zero is the transparent one, and it has to stay transparent or
    the layer paints the whole world over."""
    png = fch.construction_png([(0.0, 0.0)], 64)
    assert png.startswith(b"\x89PNG")
    at = png.index(b"tRNS")
    length = struct.unpack_from(">I", png, at - 4)[0]
    alpha = png[at + 4:at + 4 + length]
    assert alpha[0] == 0
    assert all(a > 0 for a in alpha[1:])


def test_a_lone_building_is_dimmer_than_a_settlement():
    """One hut must not look like a city. The first attempt saturated at a
    single piece and drew every outpost as the same white blob."""
    edge = 128
    lone = fch.construction_png([(0.0, 0.0)], edge)
    town = fch.construction_png([(0.0, 0.0)] * 30, edge)
    assert len(town) >= len(lone)
    assert fch.GLOW_ALPHA[1] < fch.GLOW_ALPHA[-1]
    assert max(max(r) for r in fch._GLOW) < fch.GLOW_FULL


def test_nothing_built_draws_nothing():
    assert fch.construction_png([], 64) is not None
