"""Uploading a character, and keeping only the map.

The promise is that nothing but the fog of war and the pins is kept, and
the parser makes it structural: the map is the second chunk of the file,
and it stops there. The tests that matter are the ones where the file is
wrong, enormous, or not a character file at all.
"""
import struct
import zlib

import pytest

from tests import mkfch
from tests.conftest import ALFR, STEAM

from skald import app, fch, store

T = 1_800_000_000
UID = 8_675_309


def world(uid=UID, edge=32, fill=None, pins=()):
    explored = fill if fill is not None else [1] * (edge * edge // 2) + [0] * (edge * edge // 2)
    return {"uid": uid, "edge": edge, "explored": explored, "pins": list(pins)}


# --- reading the file --------------------------------------------------

def test_a_character_file_gives_up_its_worlds():
    got = fch.parse(mkfch.character([world()]))
    assert got["version"] == 46
    (w,) = got["worlds"]
    assert w["uid"] == UID and w["edge"] == 32
    assert sum(1 for b in w["explored"] if b) == 32 * 32 // 2


def test_pins_come_through():
    blob = mkfch.character([world(pins=[
        {"name": "Home", "x": 1.5, "z": -2.5, "type": 1},
        {"name": "Silver", "x": 10.0, "z": 20.0, "type": 3, "crossed": True}])])
    pins = fch.parse(blob)["worlds"][0]["pins"]
    assert [p["name"] for p in pins] == ["Home", "Silver"]
    assert pins[1]["crossed"] is True
    assert pins[0]["x"] == 1.5 and pins[0]["z"] == -2.5


def test_a_world_with_no_map_yet_is_skipped():
    got = fch.parse(mkfch.character([{"uid": 1}, world()]))
    assert [w["uid"] for w in got["worlds"]] == [UID]


def test_several_worlds():
    got = fch.parse(mkfch.character([world(uid=1), world(uid=2), world(uid=3)]))
    assert [w["uid"] for w in got["worlds"]] == [1, 2, 3]


# --- files that are not what they claim --------------------------------

def test_rubbish_is_refused():
    for blob in [b"", b"not a character file at all", b"\x00" * 64,
                 struct.pack("<i", 1 << 30) + b"short"]:
        with pytest.raises(fch.Bad):
            fch.parse(blob)


def test_a_truncated_file_is_refused():
    blob = mkfch.character([world()])
    with pytest.raises(fch.Bad):
        fch.parse(blob[:len(blob) // 2])


def test_an_absurd_map_size_is_not_read_as_a_map():
    """A hostile file must not be able to ask for a gigabyte.

    Finding the map by its shape changes what "refused" means here: a size
    nothing could satisfy simply fails to match, so the file parses with no
    worlds in it rather than raising. Either is safe; nothing is allocated
    on the strength of a number in the file.
    """
    d = struct.pack("<i", 46) + struct.pack("<4i", 0, 0, 0, 0)
    d += struct.pack("<i", 1) + struct.pack("<q", 1) + bytes(39)
    d += struct.pack("<3f", 0, 0, 0) + b"\x01"
    d += struct.pack("<i", 4) + struct.pack("<i", 1 << 20)     # a million a side
    d += bytes(4096)
    blob = struct.pack("<i", len(d)) + d
    assert fch.parse(blob)["worlds"] == []


def test_an_absurd_pin_count_is_not_read_as_a_map():
    edge = 32
    body = struct.pack("<i", 4) + struct.pack("<i", edge) + bytes(edge * edge)
    body += struct.pack("<i", 1 << 30)                          # a billion pins
    d = struct.pack("<i", 46) + struct.pack("<4i", 0, 0, 0, 0)
    d += struct.pack("<i", 1) + struct.pack("<q", 1) + bytes(39)
    d += struct.pack("<3f", 0, 0, 0) + b"\x01" + struct.pack("<i", len(body)) + body
    blob = struct.pack("<i", len(d)) + d
    assert fch.parse(blob)["worlds"] == []


# --- packing and merging -----------------------------------------------

def test_packing_is_lossless():
    pixels = [1, 0, 1, 1, 0, 0, 0, 1] * 8
    packed = fch.pack(pixels)
    assert len(packed) == 8
    assert fch.unpack(packed, len(pixels)) == bytes(pixels)


def test_two_people_see_more_than_one():
    a = fch.pack([1, 1, 0, 0, 0, 0, 0, 0])
    b = fch.pack([0, 0, 1, 1, 0, 0, 0, 0])
    both = fch.merge(a, b)
    assert fch.unpack(both, 8) == bytes([1, 1, 1, 1, 0, 0, 0, 0])


def test_merging_with_nothing_keeps_everything():
    a = fch.pack([1, 0, 1, 0, 0, 0, 0, 0])
    assert fch.merge(a, b"") == a and fch.merge(b"", a) == a


# --- the image ---------------------------------------------------------

def test_the_png_is_a_png():
    edge = 16
    bits = fch.pack([1] * (edge * edge))
    png = fch.png(bits, edge)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"IHDR" in png and b"PLTE" in png and png[-8:-4] == b"IEND"
    width, height, depth, colour = struct.unpack(">IIBB", png[16:26])
    assert (width, height, depth, colour) == (edge, edge, 1, 3)


def test_the_png_says_what_was_explored():
    """Decoding it back out again: the image is the data, not a picture of it."""
    edge = 8
    pixels = [1 if x < 4 else 0 for _ in range(edge) for x in range(edge)]
    png = fch.png(fch.pack(pixels), edge)
    start = png.index(b"IDAT") + 4
    end = png.index(b"IEND") - 8
    raw = zlib.decompress(png[start:end])
    row = raw[1:2]                      # first scanline, after its filter byte
    assert row == bytes([0b11110000])   # four seen, four not, MSB first


# --- storage -----------------------------------------------------------

def test_a_map_is_stored_and_merged(tracker):
    store.put_user(app.db(), STEAM[ALFR], {"display_name": "a"}, T)
    store.put_user(app.db(), STEAM["Bera"], {"display_name": "b"}, T)
    a = fch.pack([1, 1, 0, 0, 0, 0, 0, 0])
    b = fch.pack([0, 0, 1, 1, 0, 0, 0, 0])
    store.put_map(app.db(), STEAM[ALFR], UID, 8, zlib.compress(a), [], 2, T)
    store.put_map(app.db(), STEAM["Bera"], UID, 8, zlib.compress(b), [], 2, T)
    merged = app.merged_map(UID)
    assert merged["people"] == 2 and merged["seen"] == 4
    assert fch.unpack(merged["bits"], 8) == bytes([1, 1, 1, 1, 0, 0, 0, 0])


def test_uploading_again_replaces_rather_than_doubles(tracker):
    store.put_user(app.db(), STEAM[ALFR], {"display_name": "a"}, T)
    for seen in (2, 5):
        store.put_map(app.db(), STEAM[ALFR], UID, 8,
                      zlib.compress(fch.pack([1] * seen + [0] * (8 - seen))),
                      [], seen, T)
    rows = store.maps_for(app.db(), UID)
    assert len(rows) == 1 and rows[0]["seen"] == 5


def test_removing_your_map_removes_only_yours(tracker):
    store.put_user(app.db(), STEAM[ALFR], {"display_name": "a"}, T)
    store.put_user(app.db(), STEAM["Bera"], {"display_name": "b"}, T)
    for who in (STEAM[ALFR], STEAM["Bera"]):
        store.put_map(app.db(), who, UID, 8, zlib.compress(fch.pack([1] * 8)), [], 8, T)
    store.drop_map(app.db(), STEAM[ALFR], UID)
    assert [r["steam_id"] for r in store.maps_for(app.db(), UID)] == [STEAM["Bera"]]


# --- shapes taken from real character files ------------------------------
#
# The parser was first written from a published description of version 33
# and refused every real file, which are version 46. These encode what an
# actual file turned out to look like, so that particular mistake cannot be
# made twice. No real file is in this repository; the shapes are.

def test_the_layout_a_real_file_uses():
    """Version 46: the map is a length-prefixed byte array."""
    blob = mkfch.character([world(uid=5097231993, edge=64)])
    got = fch.parse(blob)
    assert got["version"] == 46
    assert [w["uid"] for w in got["worlds"]] == [5097231993]


def test_the_older_layout_still_reads():
    """Finding the map by its shape rather than by walking to it means the
    version 33 arrangement -- no length in front of the map -- still works."""
    blob = mkfch.character([world(uid=42, edge=64)], version=33, legacy_map=True)
    got = fch.parse(blob)
    assert [w["uid"] for w in got["worlds"]] == [42]


def test_an_absent_point_still_occupies_its_bytes():
    """The mistake that read two of three real worlds as id 0.

    An optional point that is not set still takes its twelve bytes, zeroed.
    Assuming it collapses to a single flag byte puts the id in the wrong
    place -- and since the id is the key a map is stored under, two worlds
    become one and overwrite each other.
    """
    blob = mkfch.character([
        world(uid=1001218399, edge=64),                                  # no points
        world(uid=3289189326, edge=64, pins=[]),                         # no points
        dict(world(uid=5097231993, edge=64), spawn=(1.0, 2.0, 3.0),
             logout=(4.0, 5.0, 6.0), death=(7.0, 8.0, 9.0)),             # all three
    ])
    uids = [w["uid"] for w in fch.parse(blob)["worlds"]]
    assert uids == [1001218399, 3289189326, 5097231993]
    assert len(set(uids)) == 3, "two worlds must never share an id"


def test_a_character_who_has_been_nowhere_is_not_an_error():
    """Real files exist with no map data at all -- a small one was the first
    thing this was tried on."""
    blob = mkfch.character([{"uid": 7}, {"uid": 8}])
    assert fch.parse(blob) == {"version": 46, "worlds": []}
