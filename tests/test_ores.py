"""What is still in the ground, and the switch that keeps it hidden.

Silver is buried and meant to be hunted with a wishbone. Drawing every vein
on the map retires that, which is a decision about somebody's game rather
than about software -- so it is off unless an admin turns it on, and unticked
even then.
"""
import struct

import pytest

from skald import config, fch
from skald.worldgen import stable_hash


def _chunk(deposits):
    out = bytearray(b"\x00" * 8)
    for prefab, x, z in deposits:
        out += struct.pack("<3f", x, 120.0, z)
        out += struct.pack("<i", stable_hash(prefab))
        out += b"\x11\x22"
    return bytes(out)


def _pats():
    return {struct.pack("<i", stable_hash(prefab)): ore
            for ore, prefab in fch.ORES.items()}


def test_deposits_are_read_by_kind(tmp_path):
    path = tmp_path / "a.chunk"
    path.write_bytes(_chunk([("silvervein", 100.0, 200.0),
                             ("silvervein", 140.0, 220.0),
                             ("MineRock_Tin", -50.0, 60.0)]))
    got = fch._ores_in(str(path), _pats())
    assert sorted(got) == ["silver", "tin"]
    assert len(got["silver"]) == 2
    assert [round(v) for v in got["silver"][0]] == [100, 200]


def test_one_impossible_point_does_not_lose_the_others(tmp_path):
    body = bytearray(_chunk([("silvervein", 10.0, 10.0)]))
    body += struct.pack("<3f", 9e9, 120.0, 0.0) + struct.pack("<i", stable_hash("silvervein"))
    body += _chunk([("silvervein", 30.0, 30.0)])
    path = tmp_path / "b.chunk"
    path.write_bytes(bytes(body))
    got = fch._ores_in(str(path), _pats())
    assert len(got["silver"]) == 2


def test_a_world_with_no_ore_reads_as_nothing(tmp_path):
    path = tmp_path / "c.chunk"
    path.write_bytes(b"no ore here at all" * 50)
    assert fch._ores_in(str(path), _pats()) == {}


def test_the_drawing_is_transparent_where_there_is_none():
    png = fch.ore_png([(0.0, 0.0)], 64, (200, 100, 50))
    assert png.startswith(b"\x89PNG")
    at = png.index(b"tRNS")
    length = struct.unpack_from(">I", png, at - 4)[0]
    alpha = png[at + 4:at + 4 + length]
    assert alpha[0] == 0 and alpha[1] == 255


@pytest.mark.parametrize("value,wanted", [
    (None, False), ("", False), ("0", False), ("no", False), ("false", False),
    ("1", True), ("true", True), ("yes", True), ("on", True),
])
def test_the_switch_defaults_to_off(value, wanted):
    """Absent means off, and so does the word false -- which is what someone
    who wrote it plainly meant, not the opposite."""
    env = {} if value is None else {"SKALD_SHOW_ORES": value}
    assert config.load(path="/nonexistent", env=env).show_ores is wanted


def test_nothing_is_read_when_the_switch_is_off(monkeypatch, tmp_path):
    """Not merely hidden: with it off the saves are never scanned for ore and
    the page is never told there is any."""
    from skald import app
    monkeypatch.setattr(app, "CONFIG", config.load(path="/nonexistent", env={}))
    monkeypatch.setattr(app, "WORLD_ORES", {"Jotunheim": {"silver": [(0.0, 0.0)]}})
    assert app.ore_png("Jotunheim", "silver") is None
