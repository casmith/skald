"""Reading a Valheim character file, and stopping as soon as we have enough.

A `.fch` is a length-prefixed blob followed by a SHA-512 of itself. Inside,
the fields arrive in a fixed order, and the part we want -- the per-world
map -- is the *second* chunk, right after five integers. Everything after
it is the player: their name, inventory, skills, appearance, journal.

So this parser reads the header and the world data and then stops. Not as
an optimisation: it is the privacy promise made structural. Skald cannot
leak an inventory it has never decoded, and the file's remaining bytes are
never looked at, let alone stored.

Layout, for anyone checking this against a file:

    i32 data length, then that many bytes, then i32 + SHA-512

    i32   version (33 at the time of writing)
    i32   kills, deaths, crafts, builds
    i32   world count
    per world:
      i64   world uid
      u8    have spawn point   + 3 floats if set
      u8    have logout point  + 3 floats if set
      u8    have death point   + 3 floats if set
      f32x3 home point (always)
      u8    have map data
      if so:
        i32   map version (4)
        i32   edge length -- the map is edge*edge bytes, one per pixel
        u8[]  explored, one byte per pixel, non-zero where seen
        i32   pin count, then per pin: string name, 3 floats, i32 type, u8 crossed
        u8    position shared publicly
"""
import struct

MAX_EDGE = 4096          # 2048 today; a sanity bound, not a prediction
MAX_WORLDS = 128
MAX_PINS = 20000


class Bad(ValueError):
    """The file is not one we understand. The message is shown to a person."""


class Reader:
    def __init__(self, data):
        self.d, self.i = data, 0

    def take(self, n):
        if n < 0 or self.i + n > len(self.d):
            raise Bad("the file ends sooner than it says it does")
        out = self.d[self.i:self.i + n]
        self.i += n
        return out

    def u8(self):
        return self.take(1)[0]

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def i64(self):
        return struct.unpack("<q", self.take(8))[0]

    def f32x3(self):
        return struct.unpack("<3f", self.take(12))

    def string(self):
        """Length as a 7-bit encoded int, then UTF-8 -- .NET's own format."""
        n, shift = 0, 0
        while True:
            b = self.u8()
            n |= (b & 0x7F) << shift
            if not b & 0x80:
                break
            shift += 7
            if shift > 35:
                raise Bad("a string length that cannot be right")
        return self.take(n).decode("utf-8", "replace")


def parse(blob):
    """The worlds in a character file: uid, explored bitmap, pins.

    Returns [{uid, edge, explored (bytes, one byte per pixel), pins}].
    Raises Bad with something worth showing a person.
    """
    outer = Reader(blob)
    size = outer.i32()
    if size <= 0 or size > len(blob):
        raise Bad("this does not look like a Valheim character file")
    r = Reader(outer.take(size))

    version = r.i32()
    if not 0 < version < 1000:
        raise Bad(f"unexpected character file version {version}")
    for _ in range(4):        # kills, deaths, crafts, builds
        r.i32()

    count = r.i32()
    if not 0 <= count <= MAX_WORLDS:
        raise Bad("the world list is not a world list")
    worlds = []
    for _ in range(count):
        uid = r.i64()
        for _ in range(3):    # spawn, logout, death: each optional
            if r.u8():
                r.f32x3()
        r.f32x3()             # home point, always there
        if not r.u8():
            continue          # a world visited with no map data yet
        map_version = r.i32()
        edge = r.i32()
        if not 0 < edge <= MAX_EDGE:
            raise Bad(f"a map {edge} pixels across is not one we can read")
        explored = r.take(edge * edge)
        pins = []
        pin_count = r.i32()
        if not 0 <= pin_count <= MAX_PINS:
            raise Bad("the pin list is not a pin list")
        for _ in range(pin_count):
            name = r.string()
            x, y, z = r.f32x3()
            kind = r.i32()
            crossed = bool(r.u8())
            pins.append({"name": name, "x": x, "z": z, "type": kind,
                         "crossed": crossed})
        r.u8()                # position shared publicly: not ours to keep
        worlds.append({"uid": uid, "edge": edge, "explored": explored,
                       "pins": pins, "map_version": map_version})
    return {"version": version, "worlds": worlds}


def pack(explored):
    """One byte per pixel down to one bit. A quarter of a megabyte, not two."""
    out = bytearray((len(explored) + 7) // 8)
    for i, b in enumerate(explored):
        if b:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


def unpack(bits, n):
    return bytes(1 if bits[i >> 3] & (1 << (i & 7)) else 0 for i in range(n))


def merge(packed_a, packed_b):
    """Two people's exploration, added together."""
    if not packed_a:
        return packed_b
    if not packed_b:
        return packed_a
    n = max(len(packed_a), len(packed_b))
    a = packed_a.ljust(n, b"\0")
    b = packed_b.ljust(n, b"\0")
    return bytes(x | y for x, y in zip(a, b, strict=True))


def png(packed, edge, colours=((26, 20, 14), (214, 178, 116))):
    """A 1-bit paletted PNG of an explored bitmap, written by hand.

    Paletted at one bit a pixel because that is exactly what the data is:
    a 2048-square map is 512 KB before deflate and very much less after,
    which is small enough to serve on every page load without a cache.
    """
    import zlib
    # The packed bitmap is LSB-first within each byte; PNG reads MSB-first.
    flip = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))
    stride = (edge + 7) // 8
    rows = bytearray()
    for y in range(edge):
        rows.append(0)                      # filter: none
        row = packed[y * stride:(y + 1) * stride].ljust(stride, b"\0")
        rows.extend(row.translate(flip))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", edge, edge, 1, 3, 0, 0, 0)
    palette = b"".join(bytes(c) for c in colours)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"PLTE", palette)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def world_uid(fwl):
    """The uid and name in a world's .fwl, so an upload can be matched to it."""
    r = Reader(fwl)
    size = r.i32()
    if size <= 0 or size > len(fwl):
        raise Bad("not a world metadata file")
    r = Reader(r.take(size))
    r.i32()                      # version
    name = r.string()
    r.string()                   # seed name
    r.i32()                      # seed
    return r.i64(), name
