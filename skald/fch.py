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
import array
import bisect
import math
import os
import re
import struct
import zlib

from skald.worldgen import stable_hash

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
# Enough for every file of a world or two; past that the saves have
# rolled over and the old keys are dead weight.
MAX_CACHED_SAVES = 400
# How far past the grid to look for the pin count. The run of
# 0/1 bytes can reach into the count itself, which starts small.
_PIN_SEARCH = 8

# The map texture is 2048 pixels at 12 metres each, so it reaches 12288
# metres out -- far enough to hold the Ashlands and the Deep North, which
# sit beyond the 10500 the land itself stops at. Getting this wrong scales
# the explored area against the terrain under it.
MAP_PIXEL_SIZE = 12
MAP_SPAN = 2048 * MAP_PIXEL_SIZE / 2


def _scan_files(directory, cache, read):
    """Yield each file in `directory` passed through `read`, oldest name first.

    The cache is keyed by path alone and holds (size, mtime, found), so it is
    bounded by the number of files in the directory and never needs emptying.
    Keying it by (path, size, mtime) -- which six copies of this loop each
    did -- grew a fresh entry every time a chunk was rewritten, dumped the
    whole cache on hitting a size limit, and made the pass after that re-read
    the entire world. That sawtooth is the shape of the I/O that stalled the
    host once already, and every reader added made it worse.
    """
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return
    for name in names:
        path = os.path.join(directory, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        was = cache.get(path) if cache is not None else None
        if was is not None and was[0] == st.st_size and was[1] == st.st_mtime:
            yield was[2]
            continue
        found = read(path)
        if cache is not None:
            cache[path] = (st.st_size, st.st_mtime, found)
        yield found


def _scan_chunks(directory, cache, read):
    """Every chunk's findings in one world, concatenated.

    A reader may return None to disown a whole file rather than hand back a
    half-read one -- _portals_in does, on a single implausible position. That
    is not the same as finding nothing, but both mean there is nothing here
    to add.
    """
    out = []
    for found in _scan_files(directory, cache, read):
        if found:
            out.extend(found)
    return out


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
        # Keyed on the path, holding size and mtime, so a save that has not
        # moved is not decompressed again and the cache cannot outgrow the
        # directory. It used to be keyed on all three and emptied wholesale
        # at MAX_CACHED_SAVES, which re-read every world on the next pass.
        was = cache.get(path) if cache is not None else None
        if was is not None and was[0] == st.st_size and was[1] == st.st_mtime:
            found = was[2]
        else:
            found = _world_map_in(path)
            if cache is not None:
                cache[path] = (st.st_size, st.st_mtime, found)
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


# Valheim names objects by a hash of the prefab name, so a portal is found
# by looking for the four bytes of one rather than by any text. The tests
# check these against the hash function rather than trusting the numbers.
PORTAL_PREFAB = -661882940       # GetStableHashCode("portal_wood")
PORTAL_TAG_KEY = 696029674       # GetStableHashCode("tag")

# The object's position sits twelve bytes before its prefab hash. Nothing
# in the file says so, so every portal in a chunk has to land somewhere a
# portal could be or the chunk is not read at all.
PORTAL_POS_BACK = 12
MAX_PORTALS = 4000
PORTAL_TAG_MAX = 120


def _portals_in(path):
    """Every portal in one chunk file, or None if it holds none.

    Returns [{"name", "x", "y", "z"}].
    """
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return None
    needle = struct.pack("<i", PORTAL_PREFAB)
    tag_key = struct.pack("<i", PORTAL_TAG_KEY)
    at, offsets = blob.find(needle), []
    while at != -1:
        offsets.append(at)
        at = blob.find(needle, at + 4)
    if not offsets or len(offsets) > MAX_PORTALS:
        return None

    found = []
    for n, start in enumerate(offsets):
        if start < PORTAL_POS_BACK:
            return None
        x, y, z = struct.unpack_from("<3f", blob, start - PORTAL_POS_BACK)
        # A portal has to be somewhere a portal could be. One that is not
        # says the shape is wrong, and a wrong shape anywhere means none of
        # them can be trusted -- so the whole chunk is refused.
        if not (-MAP_SPAN < x < MAP_SPAN and -MAP_SPAN < z < MAP_SPAN):
            return None
        if not -1000.0 < y < 2000.0:
            return None
        # Its tag, if it has one, lies between it and the next portal.
        end = (offsets[n + 1] - PORTAL_POS_BACK
               if n + 1 < len(offsets) else len(blob))
        name = ""
        key_at = blob.find(tag_key, start, end)
        if key_at != -1:
            r = Reader(blob)
            r.i = key_at + 4
            try:
                text = r.string()
                if len(text) <= PORTAL_TAG_MAX and text.isprintable():
                    name = text
            except (Bad, struct.error, UnicodeDecodeError):
                name = ""
        found.append({"name": name, "x": x, "y": y, "z": z})
    return found


def world_portals(directory, cache=None):
    """Every portal in a world, from its save directory.

    Portals are ordinary world objects rather than map data, so they are not
    where the cartography table is -- but they are in the same saves Skald
    already reads, and a portal with a name on it is the most useful label a
    map can carry. Two portals sharing a name are the two ends of one.
    """
    return _scan_chunks(directory, cache, _portals_in)


# What a person can build. Not every buildable in the game -- it does not
# have to be, because the creator check below is what separates building
# from masonry, so a name missing here costs one structure rather than a
# wrong map. Add freely.
BUILD_PIECES = (
    "wood_floor", "wood_floor_1x1", "wood_wall_log", "wood_wall_half",
    "wood_wall_roof", "wood_beam", "wood_beam_1", "wood_beam_26",
    "wood_beam_45", "wood_pole", "wood_pole4", "wood_pole_log",
    "wood_pole_log_4", "wood_stair", "wood_stepladder", "wood_door",
    "wood_gate", "wood_roof", "wood_roof_45", "wood_roof_top",
    "wood_roof_icorner", "wood_roof_ocorner", "wood_ledge", "wood_fence",
    "wood_dragon", "woodiron_beam", "woodiron_pole", "woodiron_wall",
    "darkwood_beam", "darkwood_roof", "darkwood_roof_45",
    "darkwood_decowall", "darkwood_arch", "stone_floor",
    "stone_floor_2x2", "stone_wall_1x1", "stone_wall_2x1",
    "stone_wall_4x2", "stone_arch", "stone_stair", "stone_pillar",
    "blackmarble_floor", "blackmarble_pillar", "piece_workbench",
    "piece_artisanstation", "piece_stonecutter", "piece_cauldron",
    "piece_cookingstation", "piece_cookingstation_iron", "piece_oven",
    "forge", "smelter", "charcoal_kiln", "blastfurnace", "windmill",
    "piece_spinningwheel", "piece_magetable", "piece_preptable",
    "piece_cartographytable", "piece_chest_wood", "piece_chest",
    "piece_chest_private", "piece_chest_blackmetal", "piece_bed02",
    "piece_chair", "piece_throne01", "piece_table", "piece_bench01",
    "piece_walltorch", "piece_groundtorch", "piece_groundtorch_wood",
    "piece_brazierceiling01", "fire_pit", "hearth", "bonfire",
    "piece_banner01", "piece_sign", "sign", "itemstand",
    "piece_sharpstakes", "piece_beehive", "portal_wood",
)

# The pieces that actually burn. A hall with a hearth, four torches and a
# forge is a brighter place at night than a hall of the same size with
# none, so these count for more when the map is drawn as lights.
LIGHT_PIECES = frozenset((
    "fire_pit", "hearth", "bonfire", "piece_walltorch", "piece_groundtorch",
    "piece_groundtorch_wood", "piece_brazierceiling01", "piece_oven",
    "smelter", "charcoal_kiln", "blastfurnace", "piece_cookingstation",
    "piece_cookingstation_iron", "windmill", "piece_spinningwheel",
))
LIGHT_WEIGHT = 6        # a fire counts for this many walls


# A piece someone placed records who placed it. The world's own ruins and
# dungeons are made of the same prefabs and carry no creator, and there are
# far more of them -- on one of these worlds, fifty thousand generated
# pieces against under three thousand built ones. Without this the map shows
# masonry rather than settlement, and the scatter of every ruin in the world
# drowns the places people actually live.
CREATOR_KEY = 881008290          # GetStableHashCode("creator")

# How far after a piece its creator may sit. Objects are tens of bytes, so
# this reaches past the record's own fields without reaching the next piece.
CREATOR_WINDOW = 250


def _construction_in(path, prefabs):
    """Where somebody built something, in one chunk. [(x, z, is_light)].

    `prefabs` maps each prefab's packed hash to whether it burns.
    """
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return []
    lit, pieces = {}, []
    for needle, burns in prefabs.items():
        at = blob.find(needle)
        while at != -1:
            pieces.append(at)
            lit[at] = burns
            at = blob.find(needle, at + 4)
    if not pieces:
        return []
    pieces.sort()

    key = struct.pack("<i", CREATOR_KEY)
    built = set()
    at = blob.find(key)
    while at != -1:
        # The piece this creator belongs to is the last one before it.
        i = bisect.bisect_left(pieces, at) - 1
        if i >= 0 and at - pieces[i] < CREATOR_WINDOW:
            built.add(pieces[i])
        at = blob.find(key, at + 4)

    out = []
    for start in sorted(built):
        if start < PORTAL_POS_BACK:
            continue
        x, y, z = struct.unpack_from("<3f", blob, start - PORTAL_POS_BACK)
        # Unlike the portals, a bad point here is dropped on its own rather
        # than condemning the file. Hundreds of prefabs are searched for
        # instead of one, so the odd four bytes will land by chance, and
        # throwing away a world's building over three of them would be the
        # wrong trade.
        if not (-MAP_SPAN < x < MAP_SPAN and -MAP_SPAN < z < MAP_SPAN):
            continue
        if not -1000.0 < y < 2000.0:
            continue
        out.append((x, z, lit.get(start, False)))
    return out


def world_construction(directory, cache=None):
    """Everywhere somebody has built something, in one world."""
    prefabs = {struct.pack("<i", stable_hash(n)): n in LIGHT_PIECES
               for n in BUILD_PIECES}
    return _scan_chunks(directory, cache,
                        lambda path: _construction_in(path, prefabs))


# The light each piece casts. Stamped rather than blurred: a blur over four
# million pixels in Python costs seconds, and a few thousand stamps of a
# small kernel gives the same soft edge for a few hundred thousand adds.
_GLOW = (
    (0, 0, 0, 1, 1, 1, 0, 0, 0),
    (0, 1, 2, 3, 4, 3, 2, 1, 0),
    (0, 2, 4, 7, 8, 7, 4, 2, 0),
    (1, 3, 7, 12, 15, 12, 7, 3, 1),
    (1, 4, 8, 15, 20, 15, 8, 4, 1),
    (1, 3, 7, 12, 15, 12, 7, 3, 1),
    (0, 2, 4, 7, 8, 7, 4, 2, 0),
    (0, 1, 2, 3, 4, 3, 2, 1, 0),
    (0, 0, 0, 1, 1, 1, 0, 0, 0),
)

# Firelight: a faint warmth at the edge of a settlement, near-white at its
# heart. Index 0 is nothing at all and is the transparent one.
GLOW_COLOURS = (
    (0, 0, 0), (150, 70, 24), (180, 94, 30), (208, 120, 40),
    (230, 150, 56), (243, 180, 88), (250, 208, 132), (253, 230, 180),
    (255, 246, 222),
)

# The faint end has to be faint. Painted at full strength the outermost
# ring of every stamp becomes a hard orange edge, and a single hut looks
# like a city -- which is exactly what the first attempt did.
GLOW_ALPHA = (0, 45, 85, 125, 160, 190, 215, 235, 255)

# What counts as bright. One piece alone peaks at the kernel's own maximum,
# so the scale has to run well past that or everything saturates and every
# settlement is the same white blob.
GLOW_FULL = 120


# At night the halo is smaller. The wide stamp reads as firelight over a
# settlement, which is what the day view wants; from orbit a town is a point,
# and a soft edge four hundred metres across turns a village into a smudge.
_NIGHT_GLOW = (
    (0, 1, 2, 1, 0),
    (1, 5, 10, 5, 1),
    (2, 10, 20, 10, 2),
    (1, 5, 10, 5, 1),
    (0, 1, 2, 1, 0),
)

# Seen from orbit at night. A city is a white core inside an orange halo;
# a single hut on a headland is one dim ember, but still there.
NIGHT_COLOURS = (
    (0, 0, 0), (58, 26, 10), (96, 44, 14), (140, 68, 20), (184, 100, 30),
    (216, 140, 52), (238, 182, 96), (250, 218, 156), (255, 244, 214),
)
NIGHT_ALPHA = (0, 70, 110, 145, 175, 200, 225, 242, 255)
# What counts as a city, measured rather than guessed: with the stamp
# above, a settlement of a couple of thousand pieces peaks around here. The scale is absolute so two
# worlds can be compared, and logarithmic so the range from one hut to a
# capital fits in eight steps -- a hut lands around the fourth, a hamlet the
# sixth, a town the seventh, a city white.
NIGHT_FULL = 2800
_NIGHT_LOG = math.log1p(NIGHT_FULL)


def construction_png(points, edge, span=None, night=False):
    """Where people have built, as a PNG that lies over the map.

    Transparent everywhere nobody has built, so it can be laid over the
    terrain the way the explored mask is. Nothing here exaggerates: a
    settlement covers the ground it covers, and on a ten-kilometre world
    that is a small bright place in a lot of dark.

    `night` draws it as lights seen from orbit instead: the same shape, a
    wider scale so a dense town pulls away from a farmhouse, and a fire
    worth several walls, since what you would actually see at night is what
    is burning.
    """
    span = MAP_SPAN if span is None else span
    # Wider than a byte on purpose. A city of two thousand pieces and a
    # hamlet of forty both bury a byte at 255, which is why they came out
    # the same brightness: the range has to survive the counting before the
    # palette can show it.
    light = array.array("I", bytes(4 * edge * edge))
    kernel = _NIGHT_GLOW if night else _GLOW
    half = len(kernel) // 2
    for point in points:
        x, z = point[0], point[1]
        weight = LIGHT_WEIGHT if (night and len(point) > 2 and point[2]) else 1
        cx = int((x + span) / (2 * span) * edge)
        cy = int((span - z) / (2 * span) * edge)
        for dy, row in enumerate(kernel):
            py = cy + dy - half
            if not 0 <= py < edge:
                continue
            base = py * edge
            for dx, w in enumerate(row):
                if not w:
                    continue
                px = cx + dx - half
                if 0 <= px < edge:
                    at = base + px
                    light[at] += w * weight

    colours = NIGHT_COLOURS if night else GLOW_COLOURS
    alphas = NIGHT_ALPHA if night else GLOW_ALPHA
    full = NIGHT_FULL if night else GLOW_FULL
    top = len(colours) - 1
    rows = bytearray()
    for y in range(edge):
        rows.append(0)                     # PNG filter: none
        start = y * edge
        if night:
            # Logarithmic, so a hut is still a light while a city is white.
            # Linear, the city is white and everything else is nothing.
            rows += bytes(
                0 if not v else
                min(top, 1 + int((top - 1) * math.log1p(v) / _NIGHT_LOG))
                for v in light[start:start + edge])
        else:
            rows += bytes(
                0 if not v else min(top, 1 + v * (top - 1) // full)
                for v in light[start:start + edge])

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    palette = b"".join(bytes(c) for c in colours)
    alpha = bytes(alphas)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", edge, edge, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", palette)
            + chunk(b"tRNS", alpha)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


# What is still in the ground, and what it is called in the save. Only the
# four worth hunting; everything else a pick touches is scenery.
ORES = {
    "silver": "silvervein",
    "copper": "rock4_copper",
    "tin": "MineRock_Tin",
    "obsidian": "MineRock_Obsidian",
}


def _ores_in(path, pats):
    """The deposits in one chunk: {ore: [(x, z)]}."""
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return {}
    out = {}
    for needle, ore in pats.items():
        at, here = blob.find(needle), []
        while at != -1:
            if at >= PORTAL_POS_BACK:
                x, y, z = struct.unpack_from("<3f", blob, at - PORTAL_POS_BACK)
                # Dropped one at a time rather than condemning the file: four
                # patterns over tens of megabytes will collide by chance
                # eventually, and one bad point is not worth a world's ore.
                if (-MAP_SPAN < x < MAP_SPAN and -MAP_SPAN < z < MAP_SPAN
                        and -1000.0 < y < 2000.0):
                    here.append((x, z))
            at = blob.find(needle, at + 4)
        if here:
            out.setdefault(ore, []).extend(here)
    return out


def world_ores(directory, cache=None):
    """Every deposit still standing in a world: {ore: [(x, z)]}.

    Still standing is the useful part -- anything already mined is gone from
    the save, so this is what is left rather than what was ever there.
    """
    pats = {struct.pack("<i", stable_hash(prefab)): ore
            for ore, prefab in ORES.items()}
    out = {}
    for found in _scan_files(directory, cache,
                             lambda path: _ores_in(path, pats)):
        for ore, pts in found.items():
            out.setdefault(ore, []).extend(pts)
    return out


# A deposit is a point, and a point at twelve metres to the pixel is
# invisible, so each is drawn as a small cross: big enough to find, small
# enough that a seam of two thousand tin does not become a smear.
CROSS = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))
# One pixel, for things there are a thousand of. The cross is 36 metres
# across at this scale; that is right for a vein you are hunting for and
# wrong for a stump, where it turned twenty of them in a clearing into one
# blob and read as a field full of them.
DOT = ((0, 0),)


def ore_png(points, edge, colour, span=None, stamp=CROSS):
    """Points as a transparent overlay, each drawn with `stamp`."""
    span = MAP_SPAN if span is None else span
    hit = bytearray(edge * edge)
    for x, z in points:
        cx = int((x + span) / (2 * span) * edge)
        cy = int((span - z) / (2 * span) * edge)
        for dx, dy in stamp:
            px, py = cx + dx, cy + dy
            if 0 <= px < edge and 0 <= py < edge:
                hit[py * edge + px] = 1

    rows = bytearray()
    for y in range(edge):
        rows.append(0)
        rows += hit[y * edge:(y + 1) * edge]

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    palette = bytes((0, 0, 0)) + bytes(colour)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", edge, edge, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", palette)
            + chunk(b"tRNS", bytes([0, 255]))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


# A player's corpse. It exists only until somebody loots it, so what this
# finds is the stuff still lying out there.
CORPSE_PREFAB = "Player_tombstone"
OWNER_NAME_KEY = 1227488406      # GetStableHashCode("ownerName")

# Valheim builds the inside of a cave or crypt in its own space high above
# the world, directly over the entrance -- so a corpse in one has the x and
# z of the place you would walk in, and a y of about five thousand. That
# makes it mappable, and the height is worth keeping: "in the cave here" is
# a different errand from "on the ground here".
INDOORS_ABOVE = 1000.0


def _corpses_in(path):
    """The corpses in one chunk: [{name, x, z, indoors}]."""
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return []
    needle = struct.pack("<i", stable_hash(CORPSE_PREFAB))
    key = struct.pack("<i", OWNER_NAME_KEY)
    out, at = [], blob.find(needle)
    while at != -1:
        if at >= PORTAL_POS_BACK:
            x, y, z = struct.unpack_from("<3f", blob, at - PORTAL_POS_BACK)
            if (-MAP_SPAN < x < MAP_SPAN and -MAP_SPAN < z < MAP_SPAN
                    and -1000.0 < y < 8000.0):
                name = ""
                k = blob.find(key, at, min(len(blob), at + 400))
                if k != -1:
                    r = Reader(blob)
                    r.i = k + 4
                    try:
                        text = r.string()
                        if len(text) <= 60 and text.isprintable():
                            name = text
                    except (Bad, struct.error, UnicodeDecodeError):
                        name = ""
                out.append({"name": name, "x": x, "z": z,
                            "indoors": y > INDOORS_ABOVE})
        at = blob.find(needle, at + 4)
    return out


# The stumps a felled tree leaves behind. Nothing records who swung the axe:
# a stump is a destructible, not a built piece, so there is no creator field
# for the game to set, and no name anywhere in the record -- 1,375 of them
# across three worlds carried a creator in the slot a build piece keeps one
# in exactly zero times. What a stump does carry is health, a spawn time and
# sometimes a seed. So this counts them and places them, and attributes
# nothing to anybody.
STUMPS = ("FirTree_Stub", "BirchStub", "Beech_Stub", "Pinetree_01_Stub",
          "SwampTree1_Stub")


def _stumps_in(path, pats):
    """Where trees were felled in one chunk: [(x, z)]."""
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return []
    out = []
    for needle in pats:
        at = blob.find(needle)
        while at != -1:
            if at >= PORTAL_POS_BACK:
                x, y, z = struct.unpack_from("<3f", blob, at - PORTAL_POS_BACK)
                # Five prefabs are searched for by a four-byte hash, so some
                # matches are chance. A stump sits on the ground it grew on,
                # which is a narrower claim than the corpses need: no cave
                # interior five thousand metres up, and nothing underwater.
                if (-MAP_SPAN < x < MAP_SPAN and -MAP_SPAN < z < MAP_SPAN
                        and -100.0 < y < 500.0):
                    out.append((x, z))
            at = blob.find(needle, at + 4)
    return out


def world_stumps(directory, cache=None):
    """Every stump in a world: [(x, z)]. Its length is the felled count."""
    pats = tuple(struct.pack("<i", stable_hash(n)) for n in STUMPS)
    return _scan_chunks(directory, cache,
                        lambda path: _stumps_in(path, pats))


def world_corpses(directory, cache=None):
    """Every corpse still lying in a world, with whose it is."""
    return _scan_chunks(directory, cache, _corpses_in)


# Boats, by what the save calls them. They are worth finding because they
# move: a longship is wherever somebody last left it, which is not
# necessarily where you moored it.
BOATS = {
    "Raft": "raft",
    "Karve": "karve",
    "VikingShip": "longship",
    "CargoShip": "drakkar",
}


def _boats_in(path, pats):
    """The boats in one chunk: [{kind, x, z}]."""
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return []
    out = []
    for needle, kind in pats.items():
        at = blob.find(needle)
        while at != -1:
            if at >= PORTAL_POS_BACK:
                x, y, z = struct.unpack_from("<3f", blob, at - PORTAL_POS_BACK)
                # A boat sits on the water, and the water is at thirty. One
                # well off it is not a boat, it is four bytes that happened
                # to match.
                if (-MAP_SPAN < x < MAP_SPAN and -MAP_SPAN < z < MAP_SPAN
                        and 0.0 < y < 200.0):
                    out.append({"kind": kind, "x": x, "z": z})
            at = blob.find(needle, at + 4)
    return out


def world_boats(directory, cache=None):
    """Every boat in a world, and what kind each is."""
    pats = {struct.pack("<i", stable_hash(prefab)): kind
            for prefab, kind in BOATS.items()}
    return _scan_chunks(directory, cache,
                        lambda path: _boats_in(path, pats))


# The map texture is square but the world is not. Valheim's ground stops at
# 10,500 metres from the middle and the rest of the picture is a corner
# nobody can sail to, so counting it makes every explored figure smaller
# than the truth -- by a factor of about 1.7, which is the difference
# between "we have seen 4% of this world" and "we have seen 7%".
WORLD_EDGE = 10500.0

_EXPLORABLE = {}


def explorable_pixels(edge, span=None):
    """How many pixels of an `edge`-square map are inside the world at all.

    Counted a row at a time rather than a pixel at a time, and kept, since
    it depends on nothing but the size.
    """
    span = MAP_SPAN if span is None else span
    key = (edge, span)
    if key in _EXPLORABLE:
        return _EXPLORABLE[key]
    step = 2 * span / edge
    r2 = WORLD_EDGE ** 2
    total = 0
    for j in range(edge):
        y = span - (j + 0.5) * step
        if abs(y) >= WORLD_EDGE:
            continue
        half = math.sqrt(r2 - y * y)
        total += min(edge, int(2 * half / step))
    _EXPLORABLE[key] = total
    return total


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
