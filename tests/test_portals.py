"""Portals, read from the world's own saves.

A portal is an ordinary world object rather than map data, so it is not
where the cartography table is. It is found by the hash of its prefab name,
and its position sits a fixed distance before that hash -- which nothing in
the file states, so every portal in a chunk has to land somewhere a portal
could be before any of them are believed.
"""
import struct

from skald import fch
from skald.worldgen import stable_hash


def _chunk(portals):
    """A chunk holding these (name, x, y, z), in the shape the game writes."""
    out = bytearray(b"\x00" * 8)
    for name, x, y, z in portals:
        out += struct.pack("<3f", x, y, z)
        out += struct.pack("<i", fch.PORTAL_PREFAB)
        out += b"\x11\x22\x33"
        if name is not None:
            raw = name.encode("utf-8")
            out += struct.pack("<i", fch.PORTAL_TAG_KEY) + bytes([len(raw)]) + raw
        out += b"\x44\x55"
    return bytes(out)


def test_the_hashes_are_the_games_own():
    """These are the only two magic numbers here, so they are checked
    against the hash rather than trusted."""
    assert stable_hash("portal_wood") == fch.PORTAL_PREFAB
    assert stable_hash("tag") == fch.PORTAL_TAG_KEY


def test_portals_are_read_with_their_names(tmp_path):
    path = tmp_path / "a.chunk"
    path.write_bytes(_chunk([("Bogwitch", -301.0, 32.3, 241.0),
                             ("Elder", 1513.0, 40.8, 1690.0),
                             (None, 102.0, 35.6, 374.0)]))
    found = fch._portals_in(str(path))
    assert [(p["name"], round(p["x"]), round(p["z"])) for p in found] == [
        ("Bogwitch", -301, 241), ("Elder", 1513, 1690), ("", 102, 374)]


def test_a_portal_off_the_map_refuses_the_whole_chunk(tmp_path):
    """One impossible position means the shape is wrong, and a wrong shape
    anywhere makes every other portal in the file a guess."""
    path = tmp_path / "b.chunk"
    path.write_bytes(_chunk([("fine", 100.0, 30.0, 100.0),
                             ("nowhere", 999999.0, 30.0, 0.0)]))
    assert fch._portals_in(str(path)) is None


def test_a_file_with_no_portals_reads_as_none(tmp_path):
    path = tmp_path / "c.chunk"
    path.write_bytes(b"nothing of interest here" * 40)
    assert fch._portals_in(str(path)) is None


def test_the_two_ends_of_a_portal_share_a_name(tmp_path):
    """The check that says the read is right: nothing here pairs them up,
    so if the names land in pairs they were read correctly."""
    world = tmp_path / "w"
    world.mkdir()
    (world / "a.chunk").write_bytes(_chunk([("Haldor", -291.0, 32.0, 238.0)]))
    (world / "b.chunk").write_bytes(_chunk([("Haldor", 604.0, 32.1, -2870.0)]))
    got = fch.world_portals(str(world))
    assert sorted(p["name"] for p in got) == ["Haldor", "Haldor"]
    assert {round(p["x"]) for p in got} == {-291, 604}


def test_an_unreadable_tag_leaves_the_portal_nameless(tmp_path):
    """A name we cannot read is not a reason to lose the portal."""
    body = bytearray(_chunk([("ok", 10.0, 30.0, 10.0)]))
    body += struct.pack("<3f", 20.0, 30.0, 20.0)
    body += struct.pack("<i", fch.PORTAL_PREFAB)
    body += struct.pack("<i", fch.PORTAL_TAG_KEY) + b"\x04\xff\xfe\xfd\xfc"
    path = tmp_path / "d.chunk"
    path.write_bytes(bytes(body))
    found = fch._portals_in(str(path))
    assert len(found) == 2
    assert found[0]["name"] == "ok"
