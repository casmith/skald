"""Corpses, and the caves they are usually in.

A tombstone lasts until somebody loots it, so what is found is the stuff
still lying out there. The catch is where "there" is: Valheim builds the
inside of a cave or crypt in its own space about five thousand metres up.
The first read of this threw those positions away as impossible, which
happened to discard both corpses on the world it was tested against.

They are not impossible. The interior is built directly over its own
entrance, so the x and z are the ones you would walk to -- confirmed on two
worlds by finding objects at both heights over the same ground -- and the
height is what says whether somebody is inside or out on the hill.
"""
import struct

from skald import fch
from skald.worldgen import stable_hash


def _tomb(name, x, z, y=30.0):
    out = struct.pack("<3f", x, y, z)
    out += struct.pack("<i", stable_hash(fch.CORPSE_PREFAB))
    out += b"\x01\x02\x03"
    raw = name.encode("utf-8")
    out += struct.pack("<i", fch.OWNER_NAME_KEY) + bytes([len(raw)]) + raw
    return out


def test_a_corpse_is_read_with_whose_it_is(tmp_path):
    path = tmp_path / "a.chunk"
    path.write_bytes(_tomb("Hegg", 2496.0, -305.0))
    got = fch._corpses_in(str(path))
    assert len(got) == 1
    assert got[0]["name"] == "Hegg"
    assert [round(got[0]["x"]), round(got[0]["z"])] == [2496, -305]
    assert got[0]["indoors"] is False


def test_one_in_a_cave_keeps_its_place_and_says_so(tmp_path):
    """The case the first version lost: five thousand metres up is a cave
    interior, not a bad read, and its x and z are the way in."""
    path = tmp_path / "b.chunk"
    path.write_bytes(_tomb("Karlskop", 1704.0, 2561.0, y=5172.0))
    got = fch._corpses_in(str(path))
    assert len(got) == 1
    assert got[0]["indoors"] is True
    assert [round(got[0]["x"]), round(got[0]["z"])] == [1704, 2561]


def test_a_nameless_corpse_is_still_a_corpse(tmp_path):
    body = struct.pack("<3f", 10.0, 30.0, 10.0)
    body += struct.pack("<i", stable_hash(fch.CORPSE_PREFAB)) + b"\xff\xfe"
    path = tmp_path / "c.chunk"
    path.write_bytes(body)
    got = fch._corpses_in(str(path))
    assert len(got) == 1 and got[0]["name"] == ""


def test_a_position_off_the_map_is_dropped(tmp_path):
    path = tmp_path / "d.chunk"
    path.write_bytes(_tomb("nowhere", 99999.0, 0.0) + _tomb("here", 20.0, 20.0))
    got = fch._corpses_in(str(path))
    assert [c["name"] for c in got] == ["here"]


def test_a_world_where_nobody_has_died_reads_as_nothing(tmp_path):
    path = tmp_path / "e.chunk"
    path.write_bytes(b"peaceful" * 200)
    assert fch._corpses_in(str(path)) == []
