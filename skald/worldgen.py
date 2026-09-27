"""Valheim's world, generated from its seed.

Everything in a Valheim world follows from one integer. The game never
stores its terrain -- that is why a ten-kilometre world fits in a few
megabytes -- it regenerates it on demand from noise seeded by that number.
Do the same arithmetic and you get the same world, which is what makes a
real map possible from a save that contains no map.

This is a port of the biome half of it: enough to say what is where, not
how high it stands. Heights are a second, larger job and a coloured biome
map does not need them.

Three things have to be exactly right or the world is merely plausible:

  * **Unity's Perlin noise**, which is Ken Perlin's 2002 improved noise with
    two Unity peculiarities -- the inputs go through abs(), mirroring the
    field across both axes, and the result is rescaled by (raw + 0.69)/1.483.
  * **Unity's random number generator**, an xorshift128 seeded through a
    Borosh-Niederreiter multiply, and the *order* the world's offsets are
    drawn in: offset0..3, then two seeds that are thrown away here, then
    offset4 last. Draw them in any other order and you get a different
    world, silently.
  * **The base height accumulating in double.** It is the only generator
    function that does; running it in single throughout moves terrain by a
    centimetre, which sounds harmless until it flips a pixel from ocean to
    mountain on a coastline.

Ported from the Rust implementation at github.com/aritropaul/vegvisr, whose
notes on those precision traps saved this from being subtly wrong.
"""
import math

from skald.perm import PERM

WORLD_SIZE = 10000.0
WATER_EDGE = 10500.0
ASHLANDS_Y_OFFSET = -4000.0
DEEP_NORTH_Y_OFFSET = 4000.0
POLAR_MIN_DISTANCE = 12000.0

BIOMES = ("Meadows", "BlackForest", "Swamp", "Mountain", "Plains", "Ocean",
          "Mistlands", "AshLands", "DeepNorth")


def _p(i):
    return PERM[i & 255]


def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _lerp(t, a, b):
    return a + t * (b - a)


def _grad(h, x, y):
    h &= 15
    u = x if h < 8 else y
    v = y if h < 4 else (x if h in (12, 14) else 0.0)
    return (u if not h & 1 else -u) + (v if not h & 2 else -v)


def perlin(x, y):
    """UnityEngine.Mathf.PerlinNoise."""
    x, y = abs(x), abs(y)
    xi, yi = math.floor(x), math.floor(y)
    xw, yw = int(xi), int(yi)
    x -= xi
    y -= yi
    a = _p(xw) + yw
    b = _p(xw + 1) + yw
    aa, ba = _p(_p(a)), _p(_p(b))
    ab, bb = _p(_p(a + 1)), _p(_p(b + 1))
    u, v = _fade(x), _fade(y)
    res = _lerp(v,
                _lerp(u, _grad(aa, x, y), _grad(ba, x - 1.0, y)),
                _lerp(u, _grad(ab, x, y - 1.0), _grad(bb, x - 1.0, y - 1.0)))
    return (res + 0.69) / 1.483


BOROSH = 0x6C078965
MASK32 = 0xFFFFFFFF


class Random:
    """UnityEngine.Random: xorshift128, Borosh-Niederreiter seeding."""

    def __init__(self, seed):
        s0 = seed & MASK32
        s1 = (BOROSH * s0 + 1) & MASK32
        s2 = (BOROSH * s1 + 1) & MASK32
        s3 = (BOROSH * s2 + 1) & MASK32
        self.s = [s0, s1, s2, s3]

    def next(self):
        s0, s1, s2, s3 = self.s
        t = (s0 ^ (s0 << 11)) & MASK32
        # The third slot takes the OLD s3: the shift happens before s3 is
        # replaced, and using the new value gives a stream that looks random
        # and is not Unity's.
        nxt = (s3 ^ (s3 >> 19) ^ t ^ (t >> 8)) & MASK32
        self.s = [s1, s2, s3, nxt]
        return nxt

    def range_int(self, lo, hi):
        """Random.Range(int, int), max-exclusive.

        Plain modulo, no rejection sampling: Unity's modulo bias is part of
        the answer, and removing it would give a different world.
        """
        r = self.next()
        if hi == lo:
            return lo
        return (lo - r % (hi - lo)) if hi < lo else (lo + r % (hi - lo))


def stable_hash(text):
    """C#'s string.GetStableHashCode, as Valheim hashes a seed name."""
    units = text.encode("utf-16-le")
    u = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
    num = num2 = 5381
    i = 0
    while i < len(u) and u[i] != 0:
        num = (((num << 5) + num) & 0xFFFFFFFF) ^ u[i]
        if i == len(u) - 1 or u[i + 1] == 0:
            break
        num2 = (((num2 << 5) + num2) & 0xFFFFFFFF) ^ u[i + 1]
        i += 2
    out = (num + num2 * 1566083941) & 0xFFFFFFFF
    return out - (1 << 32) if out >= (1 << 31) else out


def seed_from_name(name):
    return 0 if not name else stable_hash(name)


def _clamp01(v):
    return 1.0 if v > 1.0 else (0.0 if v < 0.0 else v)


def _lerp_step(lo, hi, v):
    return _clamp01((v - lo) / (hi - lo))


def _smooth_step(lo, hi, x):
    t = _clamp01((x - lo) / (hi - lo))
    return t * t * (3.0 - 2.0 * t)


class World:
    """One world: its offsets, and what biome is where."""

    def __init__(self, seed, world_gen_version=2):
        self.seed = seed
        self.min_mountain_distance = 1000.0 if world_gen_version > 0 else 1500.0
        if world_gen_version <= 1:
            self.min_darkland_noise, self.max_marsh_distance = 0.5, 8000.0
        else:
            self.min_darkland_noise, self.max_marsh_distance = 0.4, 6000.0
        r = Random(seed)
        # The order matters and is not obvious: four offsets, two seeds we do
        # not need, then the fifth offset.
        self.offset0 = float(r.range_int(-10000, 10000))
        self.offset1 = float(r.range_int(-10000, 10000))
        self.offset2 = float(r.range_int(-10000, 10000))
        self.offset3 = float(r.range_int(-10000, 10000))
        r.range_int(-2147483648, 2147483647)      # river seed
        r.range_int(-2147483648, 2147483647)      # stream seed
        self.offset4 = float(r.range_int(-10000, 10000))

    @classmethod
    def from_name(cls, name, world_gen_version=2):
        return cls(seed_from_name(name), world_gen_version)

    def base_height(self, wx, wy):
        """The land's shape before any biome has its say.

        The one generator function that keeps its coordinates in double all
        the way to the noise call.
        """
        dist = math.hypot(wx, wy)
        x = wx + 100000.0 + self.offset0
        y = wy + 100000.0 + self.offset1
        A, B = 0.0020000000949949026, 0.003000000026077032
        C, D = 0.004999999888241291, 0.009999999776482582

        h = 0.0
        h = h + perlin(x * A * 0.5, y * A * 0.5) * perlin(x * B * 0.5, y * B * 0.5)
        h = h + perlin(x * A, y * A) * perlin(x * B, y * B) * h * 0.8999999761581421
        h = h + perlin(x * C, y * C) * perlin(x * D, y * D) * 0.5 * h
        h = h - 0.07000000029802322

        n4 = perlin(x * A * 0.25 + 0.12300000339746475,
                    y * A * 0.25 + 0.15123000741004944)
        n5 = perlin(x * A * 0.25 + 0.32100000977516174,
                    y * A * 0.25 + 0.23100000619888306)
        v = abs(n4 - n5)
        mask = (1.0 - _lerp_step(0.02, 0.12, v)) * _smooth_step(744.0, 1000.0, dist)
        h *= 1.0 - mask

        if dist > WORLD_SIZE:
            h = _lerp(_lerp_step(WORLD_SIZE, WATER_EDGE, dist), h, -0.2)
            if dist > 10490.0:
                h = _lerp(_lerp_step(10490.0, WATER_EDGE, dist), h, -2.0)
            return h

        if dist < self.min_mountain_distance and h > 0.28:
            t = _clamp01((h - 0.2800000011920929) / 0.09999999403953552)
            h = _lerp(_lerp_step(self.min_mountain_distance - 400.0,
                                 self.min_mountain_distance, dist),
                      _lerp(t, 0.28, 0.38), h)
        return h

    def world_angle(self, wx, wy):
        # atan2(x, y), not the usual way round.
        return math.sin(math.atan2(wx, wy) * 20.0)

    def biome(self, wx, wy):
        """Which biome stands at this point. A chain of thresholds, in order."""
        dist = math.hypot(wx, wy)
        base = self.base_height(wx, wy)
        a = self.world_angle(wx, wy) * 100.0

        if math.hypot(wx, wy + ASHLANDS_Y_OFFSET) > POLAR_MIN_DISTANCE + a:
            return "AshLands"
        if base <= 0.02:
            return "Ocean"
        if math.hypot(wx, wy + DEEP_NORTH_Y_OFFSET) > POLAR_MIN_DISTANCE + a:
            return "Mountain" if base > 0.4 else "DeepNorth"
        if base > 0.4:
            return "Mountain"
        if (perlin((self.offset0 + wx) * 0.001, (self.offset0 + wy) * 0.001) > 0.6
                and 2000.0 < dist < self.max_marsh_distance and 0.05 < base < 0.25):
            return "Swamp"
        if (perlin((self.offset4 + wx) * 0.001, (self.offset4 + wy) * 0.001)
                > self.min_darkland_noise and 6000.0 + a < dist < 10000.0):
            return "Mistlands"
        if (perlin((self.offset1 + wx) * 0.001, (self.offset1 + wy) * 0.001) > 0.4
                and 3000.0 + a < dist < 8000.0):
            return "Plains"
        if (perlin((self.offset2 + wx) * 0.001, (self.offset2 + wy) * 0.001) > 0.4
                and 600.0 + a < dist < 6000.0):
            return "BlackForest"
        if dist > 5000.0 + a:
            return "BlackForest"
        return "Meadows"


# What each biome looks like. Chosen to read at a glance rather than to
# match the game's own map, which is painted from terrain colours we do not
# compute.
COLOURS = {
    "Meadows": (126, 166, 88), "BlackForest": (60, 89, 58),
    "Swamp": (90, 84, 66), "Mountain": (226, 229, 235),
    "Plains": (196, 178, 102), "Ocean": (46, 78, 112),
    "Mistlands": (84, 84, 102), "AshLands": (140, 58, 44),
    "DeepNorth": (206, 222, 232),
}
# Drawn to the same reach as the game's map texture, 2048 pixels of 12
# metres, so the explored mask lies over the terrain without scaling. The
# land stops at 10500; the rest is the ocean the poles sit in.
MAP_SPAN = 2048 * 12 / 2


def render(world, size=2048, progress=None):
    """The world as an RGB PNG, `size` pixels square.

    Slow -- about fifty thousand points a second, each one a dozen noise
    samples -- and worth caching for ever, because a seed's terrain cannot
    change. Valheim's own in-game map is 2048 square for this same area, so
    there is nothing to gain by going finer.
    """
    import struct
    import zlib

    step = 2 * MAP_SPAN / size
    rows = bytearray()
    for j in range(size):
        wy = MAP_SPAN - (j + 0.5) * step
        rows.append(0)                     # PNG filter: none
        for i in range(size):
            wx = -MAP_SPAN + (i + 0.5) * step
            rows += bytes(COLOURS[world.biome(wx, wy)])
        if progress and j % 64 == 0:
            progress(j, size)

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))
