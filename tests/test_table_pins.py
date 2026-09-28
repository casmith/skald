"""Pins from a world's cartography table.

The table shares pins the way it shares the ground, so they arrive with the
map and nobody has to upload anything. They sit straight after the explored
grid, and the only thing that distinguishes them from four megabytes of
neighbouring bytes is that they account for the region exactly.
"""
import gzip
import struct

from skald import fch


def _string(text):
    """A .NET length-prefixed string."""
    raw = text.encode("utf-8")
    n, out = len(raw), bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            break
    return bytes(out) + raw


def _pin(name, x, z, kind=3, crossed=False, owner="Steam_7656119801"):
    return (struct.pack("<q", 1234)
            + _string(name)
            + struct.pack("<3f", x, 0.0, z)
            + struct.pack("<i", kind)
            + bytes([1 if crossed else 0])
            + _string(owner))


def _block(edge, pins, explored=None):
    grid = bytearray(explored or bytes(edge * edge))
    body = bytes(grid) + struct.pack("<i", len(pins)) + b"".join(pins)
    return body


def test_pins_are_read_from_the_block_after_the_grid(tmp_path):
    edge = fch.WORLD_MAP_MIN
    grid = bytearray(edge * edge)
    grid[: edge * 4] = b"\x01" * (edge * 4)
    body = _block(edge, [_pin("crypt", 120.0, -340.0),
                         _pin("$enemy_eikthyr", 2.0, 700.0, kind=9),
                         _pin("home", -50.0, 25.0, kind=1, crossed=True)],
                  explored=bytes(grid))
    path = tmp_path / "a.chunk"
    path.write_bytes(gzip.compress(body))

    found = fch._world_map_in(str(path))
    assert found is not None
    names = [(p["name"], p["type"], p["crossed"]) for p in found["pins"]]
    assert names == [("crypt", 3, False), ("$enemy_eikthyr", 9, False),
                     ("home", 1, True)]
    assert found["pins"][0]["x"] == 120.0
    assert found["pins"][0]["z"] == -340.0


def test_a_map_with_no_pins_still_reads(tmp_path):
    """A table nobody has pinned anything to is a normal table."""
    edge = fch.WORLD_MAP_MIN
    grid = bytearray(edge * edge)
    grid[:edge] = b"\x01" * edge
    path = tmp_path / "b.chunk"
    path.write_bytes(gzip.compress(_block(edge, [], explored=bytes(grid))))

    found = fch._world_map_in(str(path))
    assert found is not None
    assert found["pins"] == []


def test_bytes_that_are_not_pins_are_not_read_as_pins(tmp_path):
    """The grid is followed by junk. Nothing should be invented from it."""
    edge = fch.WORLD_MAP_MIN
    grid = bytearray(edge * edge)
    grid[:edge] = b"\x01" * edge
    body = bytes(grid) + b"\x99" * 400
    path = tmp_path / "c.chunk"
    path.write_bytes(gzip.compress(body))

    found = fch._world_map_in(str(path))
    assert found is not None
    assert found["pins"] == []


def test_a_truncated_pin_block_is_refused_whole(tmp_path):
    """Half a pin is not half a map -- it is a reason to trust none of them."""
    edge = fch.WORLD_MAP_MIN
    grid = bytes(bytearray(b"\x01" * edge) + bytearray(edge * edge - edge))
    good = _pin("crypt", 10.0, 10.0)
    body = bytes(grid) + struct.pack("<i", 2) + good + good[:-4]
    path = tmp_path / "d.chunk"
    path.write_bytes(gzip.compress(body))

    found = fch._world_map_in(str(path))
    assert found is not None
    assert found["pins"] == []


def test_a_pin_off_the_map_is_refused(tmp_path):
    """A coordinate outside the world is the clearest sign this was never a
    pin block, and it costs nothing to say so."""
    edge = fch.WORLD_MAP_MIN
    grid = bytes(bytearray(b"\x01" * edge) + bytearray(edge * edge - edge))
    body = bytes(grid) + struct.pack("<i", 1) + _pin("nowhere", 99999.0, 0.0)
    path = tmp_path / "e.chunk"
    path.write_bytes(gzip.compress(body))

    found = fch._world_map_in(str(path))
    assert found is not None
    assert found["pins"] == []
