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
import math
import os
import re
import struct
import zlib

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


# uid, then three points that are always thirteen bytes whether they
# hold anything or not, then the home point, then the map flag.
UID_BACK = 8 + 3 * 13 + 12

MIN_EDGE = 16            # smaller than any real map; the structure does the work
MAP_RUN = re.compile(rb"[\x00\x01]{%d,}" % (MIN_EDGE * MIN_EDGE))


def parse(blob):
    """The worlds in a character file: uid, explored bitmap, pins.

    Rather than walk the file field by field, this *finds* each map. The
    explored bitmap is a wholly unmistakable object -- edge*edge bytes, every
    one of them 0 or 1, four megabytes of it for a 2048-square map -- and it
    is preceded by five bytes that say so. Everything needed comes from
    around it.

    That is deliberate. The fields before the map have already been
    rearranged once: a character file written today says version 46, the
    published layout describes version 33, and walking it by the old
    description finds nonsense. The bitmap has not moved and is not going to:
    it is the one part of the file whose shape is fixed by what it is.

    Returns [{uid, edge, explored, pins}], raising Bad only when the file is
    not a character file at all. A character with no map data is not an
    error -- it is a character who has not been anywhere.
    """
    outer = Reader(blob)
    size = outer.i32()
    if size <= 0 or size > len(blob):
        raise Bad("this does not look like a Valheim character file")
    data = outer.take(size)
    version = Reader(data).i32()
    if not 0 < version < 1000:
        raise Bad(f"unexpected character file version {version}")

    worlds = []
    seen = set()
    for run in MAP_RUN.finditer(data):
        found = _map_at(data, run.start(), run.end())
        if found:
            worlds.append(found)
            seen.add(found["uid"])
    for at in _gzip_starts(data):
        found = _packed_map_at(data, at)
        if found and found["uid"] not in seen:
            worlds.append(found)
            seen.add(found["uid"])
    if len(worlds) > MAX_WORLDS:
        raise Bad("that file claims more worlds than anyone has")
    return {"version": version, "worlds": worlds}


def _map_at(data, run_start, run_end):
    """Read one map, if a run of 0/1 bytes really is one.

    The bitmap's first bytes may be indistinguishable from the tail of the
    integer that gives its size, so the exact start is searched for within a
    few bytes rather than assumed.
    """
    for start in range(max(9, run_start), min(run_start + 12, run_end)):
        edge = struct.unpack_from("<i", data, start - 4)[0]
        if not MIN_EDGE <= edge <= MAX_EDGE:
            continue
        if run_end - start < edge * edge:
            continue
        map_version = struct.unpack_from("<i", data, start - 8)[0]
        if not 0 < map_version < 100:
            continue
        # Two shapes have been seen for what sits in front of the map. In
        # the older one the "there is a map" flag butts straight up against
        # the version; in the newer the map is a byte array, so its length
        # comes between them. Accept either: which one a file uses is the
        # game's business, and the next version may invent a third.
        flag_at = None
        if data[start - 9] == 1:
            flag_at = start - 9
        else:
            length = struct.unpack_from("<i", data, start - 12)[0]
            if length >= edge * edge and data[start - 13] == 1:
                flag_at = start - 13
        if flag_at is None:
            continue
        r = Reader(data)
        r.i = start + edge * edge
        try:
            pin_count = r.i32()
            if not 0 <= pin_count <= MAX_PINS:
                continue
            pins = []
            for _ in range(pin_count):
                name = r.string()
                x, _y, z = r.f32x3()
                kind = r.i32()
                crossed = bool(r.u8())
                pins.append({"name": name, "x": x, "z": z, "type": kind,
                             "crossed": crossed})
        except Bad:
            continue
        return {"uid": _uid_before(data, flag_at), "edge": edge,
                "explored": data[start:start + edge * edge], "pins": pins,
                "map_version": map_version}
    return None


GZIP = re.compile(rb"\x1f\x8b\x08")


def _gzip_starts(data):
    return (m.start() for m in GZIP.finditer(data))


def _packed_map_at(data, at):
    """A map stored the newer way: deflated, with two bitmaps inside.

    Valheim moved the map into a gzip stream and started keeping a second
    grid beside the first -- what you uncovered, and what other players
    uncovered for you. A character created since writes every map this way,
    which is why one that plays only on a server can look, to a reader that
    only knows the older shape, like someone who has never been anywhere.

        i32 byte-array length | i32 map version | i32 deflated length | gzip
        gzip -> i32 edge | edge*edge explored | edge*edge explored by others

    The two are added together: this is for a map of where a group has
    been, and a square someone else revealed for you is a square you can
    see.
    """
    if at < 13:
        return None
    packed_len = struct.unpack_from("<i", data, at - 4)[0]
    map_version = struct.unpack_from("<i", data, at - 8)[0]
    if not 0 < map_version < 100 or packed_len <= 0:
        return None
    if data[at - 13] != 1 or at + packed_len > len(data):
        return None
    try:
        raw = zlib.decompress(data[at:at + packed_len], 31)
    except zlib.error:
        return None
    if len(raw) < 4:
        return None
    edge = struct.unpack_from("<i", raw, 0)[0]
    if not MIN_EDGE <= edge <= MAX_EDGE:
        return None
    n = edge * edge
    if len(raw) < 4 + n:
        return None
    explored = raw[4:4 + n]
    if len(raw) >= 4 + 2 * n:
        others = raw[4 + n:4 + 2 * n]
        explored = bytes(a | b for a, b in zip(explored, others, strict=True))
    return {"uid": _uid_before(data, at - 13), "edge": edge,
            "explored": explored, "pins": _pins_or_nothing(raw, 4 + 2 * n),
            "map_version": map_version}


def _pins_or_nothing(raw, at):
    """Pins if they read cleanly, nothing if they do not.

    Their shape gained a field Skald has not pinned down, and a map with no
    pins is worth having; a parser that refuses the map because it could
    not read a label is not.
    """
    if at >= len(raw):
        return []
    try:
        r = Reader(raw)
        r.i = at
        count = r.i32()
        if not 0 <= count <= MAX_PINS:
            return []
        pins = []
        for _ in range(count):
            name = r.string()
            x, _y, z = r.f32x3()
            kind = r.i32()
            crossed = bool(r.u8())
            if not name.isprintable():
                return []
            pins.append({"name": name, "x": x, "z": z, "type": kind,
                         "crossed": crossed})
        return pins
    except (Bad, struct.error):
        return []


def _uid_before(data, flag_at):
    """The world's id, read backwards from the map-data flag.

    Between the two sit the home point and three optional ones -- and an
    absent optional point still occupies its twelve bytes, zeroed, rather
    than collapsing to its flag. So the distance back is fixed:

        i64 uid | 3 x (u8 present + 12 bytes) | 12 bytes home | u8 has map

    Worth stating because assuming the other thing -- that an absent point
    takes one byte -- reads two of three worlds as id 0, and id is the key
    a map is stored under, so they would overwrite each other.

    The three flags are checked rather than trusted. A file that does not
    look like this gives 0, and a map with no id is better than two worlds
    quietly merged into one.
    """
    uid_at = flag_at - UID_BACK
    if uid_at < 0:
        return 0
    for n in range(3):
        if data[uid_at + 8 + n * 13] not in (0, 1):
            return 0
    uid = struct.unpack_from("<q", data, uid_at)[0]
    return uid if abs(uid) < 2 ** 62 else 0
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


def png(packed, edge, colours=((26, 20, 14), (214, 178, 116)), alpha=None):
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
    # `alpha` gives an opacity per palette entry. Laid over terrain, the
    # explored colour is made fully transparent so the ground shows through
    # and the rest is painted out -- unexplored means unseen, not dimmed.
    trns = chunk(b"tRNS", bytes(alpha)) if alpha else b""
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"PLTE", palette) + trns
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


# --- the cartography table, read from the world itself --------------------
#
# A world's own save holds the shared map: what everyone who used a
# cartography table has contributed. That is the group map, kept by the
# game, already on the server Skald is watching -- no upload, no character
# file, nothing that leaves the machine.
#
# It lives compressed inside one of the world's chunk files, and the bitmap
# is found the same way as in a character: a run of edge*edge bytes that are
# every one 0 or 1 is not anything else.

# The smallest grid worth believing is a map. Note there is deliberately no
# floor on the *file* size: a world nobody has explored is four megabytes of
# zeroes, which deflates to almost nothing, and skipping small files would
# skip exactly the worlds whose map has only just started.
WORLD_MAP_MIN = 256
# How far past the grid to look for the pin count. The run of
# 0/1 bytes can reach into the count itself, which starts small.
_PIN_SEARCH = 8

# The map texture is 2048 pixels at 12 metres each, so it reaches 12288
# metres out -- far enough to hold the Ashlands and the Deep North, which
# sit beyond the 10500 the land itself stops at. Getting this wrong scales
# the explored area against the terrain under it.
MAP_PIXEL_SIZE = 12
MAP_SPAN = 2048 * MAP_PIXEL_SIZE / 2


def world_map(directory, cache=None):
    """The shared map for a world, from its save directory.

    Returns {"edge", "explored", "seen", "path", "mtime"} or None. `cache`
    is an optional dict: a file whose size and mtime have not changed is not
    read again, because this means decompressing four megabytes and a world
    saves every half hour.
    """
    best = None
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return None
    for name in names:
        path = os.path.join(directory, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        key = (path, st.st_size, st.st_mtime)
        if cache is not None and key in cache:
            found = cache[key]
        else:
            found = _world_map_in(path)
            if cache is not None:
                cache.clear()          # one world, one map: do not grow
                cache[key] = found
        if found and (best is None or found["seen"] > best["seen"]):
            best = dict(found, path=path, mtime=st.st_mtime)
    return best


def _table_pins(raw, at):
    """The pins a cartography table holds, or nothing if they do not read.

    They sit straight after the explored grid, and the table shares them the
    way it shares the ground: whatever anyone has put on it. The shape is

        i32 count, then per pin:
            i64 owner, string name, f32 x, f32 y, f32 z,
            i32 type, u8 crossed, string owner id

    which is worth stating because the owner's *name* comes last, after the
    flag, and not next to the id it belongs to.

    Demanding that the count be right and that the records then consume the
    region exactly is what tells a real pin block from a coincidence: the
    odds of arbitrary bytes landing on the final byte are not worth
    worrying about.
    """
    r = Reader(raw)
    r.i = at
    try:
        count = r.i32()
        if not 0 <= count <= MAX_PINS:
            return None
        pins = []
        for _ in range(count):
            r.i64()                          # who owns it; the id follows
            name = r.string()
            x, _y, z = r.f32x3()
            kind = r.i32()
            crossed = r.u8()
            r.string()                       # "Steam_76561198..."
            if crossed not in (0, 1):
                return None
            if not (-MAP_SPAN < x < MAP_SPAN and -MAP_SPAN < z < MAP_SPAN):
                return None
            if not name.isprintable():
                return None
            pins.append({"name": name, "x": x, "z": z, "type": kind,
                         "crossed": bool(crossed)})
    except (Bad, struct.error):
        return None
    if r.i != len(raw):
        return None                          # did not account for every byte
    return pins


def _world_map_in(path):
    """The biggest explored bitmap inside one save file, if there is one."""
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return None
    best = None
    for m in GZIP.finditer(blob):
        try:
            raw = zlib.decompress(blob[m.start():], 31)
        except zlib.error:
            continue
        if len(raw) < WORLD_MAP_MIN ** 2:
            continue
        for run in MAP_RUN.finditer(raw):
            length = run.end() - run.start()
            edge = math.isqrt(length)
            if edge < WORLD_MAP_MIN or edge > MAX_EDGE:
                continue
            explored = raw[run.start():run.start() + edge * edge]
            # Unity textures start at the bottom-left; a PNG starts at the
            # top-left. Turn it over once, here, so everything downstream
            # can think in ordinary image coordinates.
            explored = b"".join(explored[y * edge:(y + 1) * edge]
                                for y in range(edge - 1, -1, -1))
            packed = pack(explored)
            seen = sum(bin(b).count("1") for b in packed)
            # The pins follow the grid. The run may have swallowed a byte or
            # two past it -- a count that begins 00 is still 0 or 1 -- so
            # the first offset that accounts for the region exactly wins.
            pins = []
            for skip in range(_PIN_SEARCH):
                found = _table_pins(raw, run.start() + edge * edge + skip)
                if found is not None:
                    pins = found
                    break
            if best is None or seen > best["seen"]:
                best = {"edge": edge, "explored": packed, "seen": seen,
                        "pins": pins}
    return best


def world_meta(fwl):
    """A world's name, seed and id, from its .fwl.

    The seed is the whole world: Valheim stores no terrain, it regenerates
    it from this number, so having it means being able to draw the map.
    """
    r = Reader(fwl)
    size = r.i32()
    if size <= 0 or size > len(fwl):
        raise Bad("not a world metadata file")
    r = Reader(r.take(size))
    version = r.i32()
    name = r.string()
    seed_name = r.string()
    seed = r.i32()
    return {"version": version, "name": name, "seed_name": seed_name,
            "seed": seed, "uid": r.i64()}
