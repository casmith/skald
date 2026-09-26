"""The cartography table, read from the world's own save.

The best version of a group map: the game already keeps one, in a file
Skald already mounts. Nobody uploads anything, nothing leaves the server,
and it covers everyone who used a table rather than everyone who could be
bothered.

It is found by shape, like a character's map -- a run of edge*edge bytes
every one of which is 0 or 1 is not anything else -- because the save is a
world of chunks with no index to the thing we want.
"""
import gzip
import struct

from skald import fch


def chunk_with_map(edge, explored, noise=b"", version=b"\x00" * 7):
    """A save file shaped like the one this was written against: a gzip
    stream buried in other data, with the bitmap a little way into it."""
    body = version + bytes(explored) + b"\x00" * 64
    return noise + gzip.compress(body) + noise


def circle(edge, radius):
    c = edge // 2
    return [1 if (x - c) ** 2 + (y - c) ** 2 < radius ** 2 else 0
            for y in range(edge) for x in range(edge)]


def test_a_world_gives_up_its_shared_map(tmp_path):
    edge = 512
    pixels = circle(edge, 60)
    (tmp_path / "20_20__1_304.chunk").write_bytes(chunk_with_map(edge, pixels))
    got = fch.world_map(str(tmp_path))
    assert got["edge"] == edge
    assert got["seen"] == sum(pixels)
    assert len(got["explored"]) == edge * edge // 8


def test_the_biggest_map_in_the_world_wins(tmp_path):
    """Chunks come in whatever order; the shared map is the fullest one."""
    edge = 512
    (tmp_path / "a.chunk").write_bytes(chunk_with_map(edge, circle(edge, 20)))
    (tmp_path / "b.chunk").write_bytes(chunk_with_map(edge, circle(edge, 80)))
    got = fch.world_map(str(tmp_path))
    assert got["seen"] == sum(circle(edge, 80))


def test_a_world_with_no_table_is_not_an_error(tmp_path):
    (tmp_path / "plain.chunk").write_bytes(b"nothing map-shaped in here" * 1000)
    assert fch.world_map(str(tmp_path)) is None
    assert fch.world_map(str(tmp_path / "does-not-exist")) is None


def test_an_unchanged_save_is_not_read_again(tmp_path):
    """A world saves every half hour and this decompresses four megabytes;
    doing that every minute for nothing would be rude."""
    edge = 512
    path = tmp_path / "a.chunk"
    path.write_bytes(chunk_with_map(edge, circle(edge, 40)))
    cache = {}
    first = fch.world_map(str(tmp_path), cache)
    assert cache, "nothing was remembered"
    calls = []
    real = fch._world_map_in
    fch._world_map_in = lambda p: (calls.append(p), real(p))[1]
    try:
        again = fch.world_map(str(tmp_path), cache)
        assert calls == [], "the file was read again although it had not changed"
        assert again["seen"] == first["seen"]
        # Now change it, and it must be read again.
        path.write_bytes(chunk_with_map(edge, circle(edge, 90)))
        changed = fch.world_map(str(tmp_path), cache)
        assert calls, "a changed save was not re-read"
        assert changed["seen"] > first["seen"]
    finally:
        fch._world_map_in = real


def test_the_map_renders_as_a_png(tmp_path):
    edge = 256
    (tmp_path / "a.chunk").write_bytes(chunk_with_map(edge, circle(edge, 50)))
    got = fch.world_map(str(tmp_path))
    png = fch.png(got["explored"], got["edge"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, colour = struct.unpack(">IIBB", png[16:26])
    assert (width, height, depth, colour) == (edge, edge, 1, 3)
