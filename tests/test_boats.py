"""Boats, because they move.

A longship is wherever somebody last left it, which is the whole reason for
finding it on a map. The check that a boat is a boat is that it floats: the
sea is at thirty metres and every karve and longship on these worlds sits
between twenty-nine and thirty-one.
"""
import struct

from skald import fch
from skald.worldgen import stable_hash


def _boat(prefab, x, z, y=30.0):
    return (struct.pack("<3f", x, y, z)
            + struct.pack("<i", stable_hash(prefab)) + b"\x07\x08")


def _pats():
    return {struct.pack("<i", stable_hash(p)): k for p, k in fch.BOATS.items()}


def test_boats_are_read_with_their_kind(tmp_path):
    path = tmp_path / "a.chunk"
    path.write_bytes(_boat("VikingShip", 4583.0, -2541.0, 29.6)
                     + _boat("Karve", 593.0, -2890.0, 30.3))
    got = sorted(fch._boats_in(str(path), _pats()), key=lambda b: b["kind"])
    assert [b["kind"] for b in got] == ["karve", "longship"]
    ship = next(b for b in got if b["kind"] == "longship")
    assert [round(ship["x"]), round(ship["z"])] == [4583, -2541]


def test_something_far_above_the_sea_is_not_a_boat(tmp_path):
    """Four bytes will match by chance eventually. A boat floats, so one
    found on a mountain top or inside a cave is not one."""
    path = tmp_path / "b.chunk"
    path.write_bytes(_boat("Karve", 100.0, 100.0, 30.0)
                     + _boat("Karve", 200.0, 200.0, 5172.0)
                     + _boat("Karve", 300.0, 300.0, 900.0))
    got = fch._boats_in(str(path), _pats())
    assert len(got) == 1
    assert [round(got[0]["x"])] == [100]


def test_a_boat_off_the_map_is_dropped(tmp_path):
    path = tmp_path / "c.chunk"
    path.write_bytes(_boat("Karve", 99999.0, 0.0) + _boat("Raft", 10.0, 10.0))
    got = fch._boats_in(str(path), _pats())
    assert [b["kind"] for b in got] == ["raft"]


def test_a_world_with_no_boats_reads_as_nothing(tmp_path):
    path = tmp_path / "d.chunk"
    path.write_bytes(b"landlocked" * 100)
    assert fch._boats_in(str(path), _pats()) == []


def test_every_boat_kind_has_a_name():
    assert set(fch.BOATS.values()) == {"raft", "karve", "longship", "drakkar"}
