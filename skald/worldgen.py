"""Valheim's world, generated from its seed.

Everything in a Valheim world follows from one integer. The game never
stores its terrain -- that is why a ten-kilometre world fits in a few
megabytes -- it regenerates it on demand from noise seeded by that number.
Do the same arithmetic and you get the same world, which is what makes a
real map possible from a save that contains no map.

This is a port of enough of it to draw a map: what biome is where, and
how high the ground stands there.

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
import itertools
import math
import struct
import threading

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

    def value(self):
        """Random.value: the low 23 bits, times the float nearest 1/(2^23-1)."""
        return _f32((self.next() & 0x7FFFFF) * 1.1920930376163765e-07)

    def range_float(self, lo, hi):
        """Random.Range(float, float), as `(1 - f) * hi + f * lo` in single
        precision -- the order the native code rounds in."""
        f = self.value()
        return _f32(_f32(_f32(1.0 - f) * hi) + _f32(f * lo))


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

    def __init__(self, seed, world_gen_version=2, rivers=True):
        self.seed = seed
        self.with_rivers = rivers
        self._rivers = None
        self.min_mountain_distance = 1000.0 if world_gen_version > 0 else 1500.0
        if world_gen_version <= 1:
            self.min_darkland_noise, self.max_marsh_distance = 0.5, 8000.0
        else:
            self.min_darkland_noise, self.max_marsh_distance = 0.4, 6000.0
        r = Random(seed)
        # The order matters and is not obvious: four offsets, the river and
        # stream seeds, then the fifth offset.
        self.offset0 = float(r.range_int(-10000, 10000))
        self.offset1 = float(r.range_int(-10000, 10000))
        self.offset2 = float(r.range_int(-10000, 10000))
        self.offset3 = float(r.range_int(-10000, 10000))
        self.river_seed = r.range_int(-2147483648, 2147483647)
        self.stream_seed = r.range_int(-2147483648, 2147483647)
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
        return self.sample(wx, wy)[0]

    def sample(self, wx, wy):
        """The biome here, and the base height the chain decided it from.

        Worth having as one call: the height layer below needs both, and the
        base height is a dozen noise samples.
        """
        dist = math.hypot(wx, wy)
        base = self.base_height(wx, wy)
        a = self.world_angle(wx, wy) * 100.0

        if math.hypot(wx, wy + ASHLANDS_Y_OFFSET) > POLAR_MIN_DISTANCE + a:
            return "AshLands", base
        if base <= 0.02:
            return "Ocean", base
        if math.hypot(wx, wy + DEEP_NORTH_Y_OFFSET) > POLAR_MIN_DISTANCE + a:
            return ("Mountain" if base > 0.4 else "DeepNorth"), base
        if base > 0.4:
            return "Mountain", base
        if (perlin((self.offset0 + wx) * 0.001, (self.offset0 + wy) * 0.001) > 0.6
                and 2000.0 < dist < self.max_marsh_distance and 0.05 < base < 0.25):
            return "Swamp", base
        if (perlin((self.offset4 + wx) * 0.001, (self.offset4 + wy) * 0.001)
                > self.min_darkland_noise and 6000.0 + a < dist < 10000.0):
            return "Mistlands", base
        if (perlin((self.offset1 + wx) * 0.001, (self.offset1 + wy) * 0.001) > 0.4
                and 3000.0 + a < dist < 8000.0):
            return "Plains", base
        if (perlin((self.offset2 + wx) * 0.001, (self.offset2 + wy) * 0.001) > 0.4
                and 600.0 + a < dist < 6000.0):
            return "BlackForest", base
        if dist > 5000.0 + a:
            return "BlackForest", base
        return "Meadows", base


    # ---- how high the ground actually is -------------------------------
    #
    # The base height above decides the *biome*; it is not the ground. Each
    # biome then shapes its own terrain, and the game's map colours water by
    # comparing that result against sea level -- not by asking whether the
    # biome is Ocean. The two are very different questions: about three
    # quarters of the world is not Ocean, and only about two fifths of it is
    # dry. Colour by biome alone and every drowned shelf paints green, which
    # turns an archipelago into a continent.

    def _detail(self, x, y):
        n = perlin(x * 0.01, y * 0.01) * perlin(x * 0.02, y * 0.02)
        return n + perlin(x * 0.05, y * 0.05) * perlin(x * 0.1, y * 0.1) * n * 0.5

    @staticmethod
    def _rough(x, y, h):
        return h + perlin(x * 0.1, y * 0.1) * 0.01 + perlin(x * 0.4, y * 0.4) * 0.003

    def _gap(self, wx, wy, y_offset):
        """Zero on a polar border, back to one over 400 metres either side.

        This scales the height *multiplier*, so it drowns the ground on both
        sides of the line. It is what makes Ashlands and the Deep North
        islands rather than a continuation of the map.
        """
        r = math.hypot(wx, wy + y_offset)
        if r < 11400.0 or r > 12600.0:
            return 1.0
        a = self.world_angle(wx, wy) * 100.0
        t = _clamp01(abs(r - (POLAR_MIN_DISTANCE + a)) / 400.0)
        t = -2.0 * t * t * t + 3.0 * t * t
        return t

    def height(self, wx, wy, biome=None, base=None):
        """The ground here, in metres. Sea level is 30."""
        if biome is None:
            biome, base = self.sample(wx, wy)
        rivers = self.rivers
        h = self._ground(wx, wy, biome, base, rivers)
        mult = (HEIGHT_MULTIPLIER
                * self._gap(wx, wy, ASHLANDS_Y_OFFSET)
                * self._gap(wx, wy, DEEP_NORTH_Y_OFFSET))
        return h * mult

    def _ground(self, wx, wy, biome, base, rivers, deep_lift=0.0):
        """Each biome's own shape, as a fraction, rivers carved in.

        Every biome but Ocean carves the rivers at the same point in its
        recipe: after its own shape, before the fine roughness.
        """
        x = wx + 100000.0 + self.offset3
        y = wy + 100000.0 + self.offset3
        carve = rivers.carve

        if biome in ("Meadows", "Plains"):
            h = base + self._detail(x, y) * 0.1
            over = h - 0.15
            if over > 0.0:                 # flatten what rises above the shore
                h -= over * (1.0 - _clamp01(base / 0.4)) * 0.75
            h = self._rough(x, y, carve(wx, wy, h))
        elif biome == "BlackForest":
            h = self._rough(x, y, carve(wx, wy, base + self._detail(x, y) * 0.1))
        elif biome == "Swamp":
            # Marsh ignores the base height entirely and sits at 0.137 --
            # 27.4 metres, just under the sea. It is bog, not ocean.
            mx, my = wx + 100000.0, wy + 100000.0
            h = self._rough(mx, my, carve(wx, wy, 0.137 + perlin(mx * 0.04, my * 0.04)
                                          * perlin(mx * 0.08, my * 0.08) * 0.03))
        elif biome == "Mountain":
            tilt = (abs(self.base_height(wx + 1.0, wy) - self.base_height(wx - 1.0, wy))
                    + abs(self.base_height(wx, wy - 1.0) - self.base_height(wx, wy + 1.0)))
            h = carve(wx, wy, base + (base - 0.4) + self._detail(x, y) * 0.2)
            h = self._rough(x, y, h)
            h += perlin(x * 0.2, y * 0.2) * 2.0 * tilt
        elif biome == "DeepNorth":
            b = base + deep_lift
            h = (b + max(0.0, b - 0.4) + self._detail(x, y) * 0.2) * 1.2
            h = carve(wx, wy, h)
            h += perlin(x * 0.1, y * 0.1) * 0.01 + perlin(x * 0.4, y * 0.4) * 0.003
        elif biome == "Mistlands":
            n = perlin(x * 0.014, y * 0.014) * perlin(x * 0.028, y * 0.028)
            n += perlin(x * 0.021, y * 0.021) * perlin(x * 0.035, y * 0.035) * n * 0.5
            if n > 0.0:
                n = n ** 1.5
            h = carve(wx, wy, base + n * 0.4)
            t = _clamp01(n * 7.0)
            h += perlin(x * 0.1, y * 0.1) * 0.03 * t
            h += perlin(x * 0.4, y * 0.4) * 0.01 * t
            h = _lerp(t, h + perlin(x * 0.4, y * 0.4) * 0.002,
                      math.ceil(h * 400.0) / 400.0)
        elif biome == "Ocean":
            h = base
        else:                              # AshLands, to its pregenerate shape
            h = carve(wx, wy, self._rough(x, y, base + self._detail(x, y) * 0.1 + 0.1))
        return h

    # ---- rivers ----------------------------------------------------------
    #
    # The game carves rivers into the ground, and they are not in the seed's
    # noise: they are laid out once per world, before any terrain is asked
    # for, from two seeds of their own. Low ground is found and clustered
    # into "lakes"; rivers join lakes; then thousands of short streams run
    # from the shore up the hillsides. Leave this out and every river is
    # drawn as dry land -- a sixty-to-a-hundred-metre channel, wide enough to
    # sail, missing from the map.

    @property
    def rivers(self):
        """The world's carved channels, laid out the first time they are
        needed: it is a few seconds of work, and only the ground needs it."""
        if self._rivers is None:
            if not self.with_rivers:
                self._rivers = _NO_RIVERS
            else:
                with _LAID_LOCK:
                    if self.seed not in _LAID:
                        _LAID[self.seed] = self._pregenerate()
                self._rivers = _LAID[self.seed]
        return self._rivers

    def pregeneration_height(self, wx, wy, rivers, river_pregen):
        """GetPregenerationHeight: what stream placement samples. No polar
        gap, Mistlands as plain forest, and the Deep North lifted by a tenth
        only on the second pass."""
        if math.hypot(wx, wy) > WATER_EDGE:
            return -2.0 * HEIGHT_MULTIPLIER
        biome, base = self.sample(wx, wy)
        if biome == "Mistlands":
            biome = "BlackForest"
        lift = 0.0 if river_pregen else 0.1
        return self._ground(wx, wy, biome, base, rivers, lift) * HEIGHT_MULTIPLIER

    def is_deep_north(self, wx, wy):
        a = self.world_angle(wx, wy) * 100.0
        return math.hypot(wx, wy + DEEP_NORTH_Y_OFFSET) > POLAR_MIN_DISTANCE + a

    def _pregenerate(self):
        rivers = Rivers()
        lakes = self._find_lakes()
        rng = Random(self.river_seed)
        rivers.render(self._place_rivers(lakes, rng), rng)

        # Streams are placed twice from the same seed, on ground that already
        # has the rivers in it: once ignoring the Deep North's lift, keeping
        # the streams that start outside it, and once with the lift, keeping
        # the ones that start inside.
        rng = Random(self.stream_seed)
        streams = self._place_streams(rng, rivers, True)
        rivers.render([s for s in streams if not self.is_deep_north(*s[0])], rng)
        rng = Random(self.stream_seed)
        streams = self._place_streams(rng, rivers, False)
        rivers.render([s for s in streams if self.is_deep_north(*s[0])], rng)
        return rivers

    def _find_lakes(self):
        """Every 128 m where the base height is under water, merged into
        clusters 800 m across."""
        pts = []
        for j in range(157):
            y = -10000.0 + j * 128.0
            for i in range(157):
                x = -10000.0 + i * 128.0
                if math.hypot(x, y) <= WORLD_SIZE and self.base_height(x, y) < 0.05:
                    pts.append((x, y))
        return _merge_points(pts, 800.0)

    def _river_allowed(self, p0, p1, step, limit):
        """No higher than `limit` anywhere along it, and below the waterline
        somewhere."""
        dist = _f32(math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
        if dist <= 0.0:
            return False
        nx, ny = _f32((p1[0] - p0[0]) / dist), _f32((p1[1] - p0[1]) / dist)
        dry = True
        t = step
        while t <= dist - step:
            h = self.base_height(_f32(p0[0] + nx * t), _f32(p0[1] + ny * t))
            if h > limit:
                return False
            if h > 0.05:
                dry = False
            t += step
        return not dry

    def _river_end(self, rivers, lakes, p, reach, rng):
        joined = {(a, b) for a, b, *_ in rivers}
        options = [i for i, q in enumerate(lakes)
                   if q != p and math.hypot(p[0] - q[0], p[1] - q[1]) < reach
                   and (p, q) not in joined and (q, p) not in joined
                   and self._river_allowed(p, q, 128.0, 0.4)]
        if not options:
            return -1
        return options[rng.range_int(0, len(options))]

    def _place_rivers(self, lakes, rng):
        rivers = []
        pending = list(lakes)
        while len(pending) > 1:
            p = pending[0]
            k = self._river_end(rivers, lakes, p, 2000.0, rng)
            if k == -1 and not any(p in (a, b) for a, b, *_ in rivers):
                k = self._river_end(rivers, lakes, p, 5000.0, rng)
            if k == -1:
                pending.pop(0)
                continue
            q = lakes[k]
            wmax = rng.range_float(60.0, 100.0)
            wmin = rng.range_float(60.0, wmax)
            d = _f32(math.hypot(q[0] - p[0], q[1] - p[1]))
            rivers.append((p, q, wmin, wmax, _f32(d / 15.0), _f32(d / 20.0)))
        return rivers

    def _place_streams(self, rng, rivers, river_pregen):
        out = []
        ground = self.pregeneration_height
        steps = 100
        shrink = _f32((200.0 - 80.0) / steps)
        tau = _f32(math.pi * 2.0)
        for _ in range(3000):
            start = None
            for _ in range(100):
                x = rng.range_float(-10000.0, 10000.0)
                y = rng.range_float(-10000.0, 10000.0)
                if 26.0 < ground(x, y, rivers, river_pregen) < 31.0:
                    start = (x, y)
                    break
            if start is None:
                continue
            end = None
            length = 200.0
            for _ in range(steps):
                length = _f32(length - shrink)
                f = rng.range_float(0.0, tau)
                x = _f32(start[0] + _f32(math.sin(f)) * length)
                y = _f32(start[1] + _f32(math.cos(f)) * length)
                if 36.0 < ground(x, y, rivers, river_pregen) < 44.0:
                    end = (x, y)
                    break
            if end is None:
                continue
            mid = ground(_f32((start[0] + end[0]) * 0.5), _f32((start[1] + end[1]) * 0.5),
                         rivers, river_pregen)
            if mid < 26.0 or mid > 44.0:
                continue
            d = _f32(math.hypot(end[0] - start[0], end[1] - start[1]))
            out.append((start, end, 20.0, 20.0, _f32(d / 15.0), _f32(d / 20.0)))
        return out


def _f32(v):
    """The nearest single-precision float, which is what the game holds."""
    return _F32.unpack(_F32.pack(v))[0]


_F32 = struct.Struct("<f")


def _merge_points(points, reach):
    """Absorb the nearest point within reach into the cluster, averaging,
    until none is left in reach; then start the next cluster."""
    points = list(points)
    out = []
    while points:
        p = points.pop(0)
        while True:
            best, best_d = -1, 99999.0
            for i, q in enumerate(points):
                if q != p:
                    d = math.hypot(p[0] - q[0], p[1] - q[1])
                    if d < reach and d < best_d:
                        best, best_d = i, d
            if best == -1:
                break
            q = points[best]
            p = (_f32((p[0] + q[0]) * 0.5), _f32((p[1] + q[1]) * 0.5))
            points[best] = points[-1]
            points.pop()
        out.append(p)
    return out


class Rivers:
    """River and stream centrelines, as points with a radius, filed in a
    64 m grid so a height only looks at the handful near it."""

    def __init__(self):
        self.grid = {}

    @staticmethod
    def _cell(v):
        return math.floor((v + 32.0) / 64.0)

    def add(self, x, y, r):
        gx, gy = self._cell(x), self._cell(y)
        span = math.ceil(r / 64.0)
        point = (x, y, r, r * r)
        for i in range(gy - span, gy + span + 1):
            for j in range(gx - span, gx + span + 1):
                if abs(x - j * 64.0) < r + 32.0 and abs(y - i * 64.0) < r + 32.0:
                    self.grid.setdefault((j, i), []).append(point)

    def render(self, rivers, rng):
        """Each centreline meanders on three sines, sampled every eighth of
        its narrowest width, each sample a random width of its own -- which
        is what makes the banks wobble."""
        for p0, p1, wmin, wmax, curve_width, wavelength in rivers:
            step = _f32(wmin / 8.0)
            dx, dy = _f32(p1[0] - p0[0]), _f32(p1[1] - p0[1])
            dist = _f32(math.hypot(dx, dy))
            if dist <= 0.0 or step <= 0.0:
                continue
            nx, ny = _f32(dx / dist), _f32(dy / dist)
            t = 0.0
            while t <= dist:
                a = _f32(t / wavelength)
                off = _f32(math.sin(a) * math.sin(a * 0.634119987487793)
                           * math.sin(a * 0.3341200053691864) * curve_width)
                r = rng.range_float(wmin, wmax)
                self.add(_f32(p0[0] + nx * t - ny * off),
                         _f32(p0[1] + ny * t + nx * off), r)
                t = _f32(t + step)

    def weight(self, wx, wy):
        """How deep in a channel this is, 0 at the bank to 1 on its line,
        and the channel's width there."""
        points = self.grid.get((self._cell(wx), self._cell(wy)))
        if not points:
            return 0.0, 0.0
        best = acc_w = acc_t = 0.0
        for x, y, r, r2 in points:
            dx, dy = x - wx, y - wy
            d2 = dx * dx + dy * dy
            if d2 < r2:
                t = 1.0 - math.sqrt(d2) / r
                if t > best:
                    best = t
                acc_w += r * t
                acc_t += t
        return best, (acc_w / acc_t if acc_t > 0.0 else 0.0)

    def carve(self, wx, wy, h):
        """AddRivers: pull the ground down to a riverbed just under the sea,
        wider rivers deeper."""
        if not self.grid:
            return h
        weight, width = self.weight(wx, wy)
        if weight <= 0.0:
            return h
        t = _lerp_step(20.0, 60.0, width)
        bed = _lerp(t, 0.14, 0.12)
        bank = _lerp(t, 0.139, 0.128)
        if h > bed:
            h = _lerp(weight, h, bed)
        if h > bank:
            h = _lerp(_lerp_step(0.85, 1.0, weight), h, bank)
        return h


_NO_RIVERS = Rivers()
# seed -> Rivers. Laying them out is seconds of work and the answer never
# changes, and the drawing and the hover lookup each make their own World.
_LAID = {}
_LAID_LOCK = threading.Lock()


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

# Base heights are fractions; the ground is 200 times one. Sea level is 30
# metres, which is why 0.15 keeps turning up as "the shore".
# Bumped whenever the drawing changes. A seed's terrain cannot change, but
# our rendering of it can, and the cache is keyed on the seed -- so without
# this a fix reaches nobody who already has the old picture on disk. It went
# to 2 with the biome colours, and to 3 when the rivers were carved in.
MODEL = 3

HEIGHT_MULTIPLIER = 200.0
SEA_LEVEL = 30.0
# Where the drawing puts the water's edge: two and a half metres under the
# sea, not at it. Measured, not derived -- the game's own map, laid over our
# heights on a real world, draws water below 27-28 m in every biome and
# land above, and 27.5 disagrees with it least. It is also what lets a
# swamp be bog without a rule of its own: marsh ground sits around 27-33 m
# and reads as land, while a river cut through it, at 24-26, reads as water.
WATERLINE = 27.5

def _mix(a, b, t):
    """`a` at t=0, `b` at t=1."""
    t = _clamp01(t)
    return tuple(int(round(c + (d - c) * t)) for c, d in zip(a, b, strict=True))


# ---- the drawing ---------------------------------------------------------
#
# The ground drawn for its shape as well as its biomes: each biome in its
# own colour, lifted a little with height, shaded as if lit from the
# north-west, with a contour every twenty metres and a heavier one every
# hundred. It needs the true height of every pixel. A flat map coloured by
# biome alone came first; this replaced it, being the same colours with the
# shape of the land added.

CONTOUR = 20.0            # metres between contour lines
INDEX_CONTOUR = 100.0     # every fifth line, drawn heavier
# A pixel is twelve metres, which flattens everything; without exaggerating
# the relief the shading would barely show outside the mountains.
RELIEF = 3.0
PIXEL_METRES = 2 * MAP_SPAN / 2048

# Land keeps its biome's colour, so a mountain is still white and a swamp
# still brown, and height only lifts it: darker at the shore, paler towards
# this many metres above the sea. The shading and the contours carry the
# shape; the colour says where you are.
HIGH = 250.0
# Depth below the sea, in metres.
SEA_RAMP = ((0.0, (178, 214, 226)), (5.0, (150, 196, 218)),
            (20.0, (112, 164, 204)), (60.0, (78, 128, 180)),
            (200.0, (52, 92, 148)))
COAST = (40, 70, 100)


def _ramp(stops, v):
    if v <= stops[0][0]:
        return stops[0][1]
    for (lo, a), (hi, b) in itertools.pairwise(stops):
        if v <= hi:
            return _mix(a, b, (v - lo) / (hi - lo))
    return stops[-1][1]


def heights(world, size=2048, progress=None):
    """The ground's height at every pixel, and which biome it is in, as an
    index into BIOMES.

    Pixels past the edge of the world are not worked out -- the page clips
    them away -- and are given the depth the edge falls to.
    """
    from array import array

    step = 2 * MAP_SPAN / size
    reach = (WATER_EDGE + 2 * step) ** 2
    hs = array("f", bytes(4 * size * size))
    biomes = bytearray([BIOMES.index("Ocean")]) * (size * size)
    index = {b: i for i, b in enumerate(BIOMES)}
    for j in range(size):
        wy = MAP_SPAN - (j + 0.5) * step
        row = j * size
        for i in range(size):
            wx = -MAP_SPAN + (i + 0.5) * step
            if wx * wx + wy * wy > reach:
                hs[row + i] = -2.0 * HEIGHT_MULTIPLIER
                continue
            biome, base = world.sample(wx, wy)
            hs[row + i] = world.height(wx, wy, biome, base)
            biomes[row + i] = index[biome]
        if progress and j % 64 == 0:
            progress(j, size)
    return hs, biomes


def topo_rows(hs, biomes, size, metres=None):
    """Raw PNG scanlines for the topographic drawing of these heights.

    `metres` is the ground one pixel covers, if it is not the whole map
    drawn `size` pixels square.
    """
    # Each biome's colour from the shore to HIGH, worked out once.
    tints = [(_mix(COLOURS[b], (0, 0, 0), 0.18),
              _mix(COLOURS[b], (255, 255, 255), 0.22)) for b in BIOMES]
    # Light from the north-west, 45 degrees up: the convention every printed
    # relief map uses, and the one that makes hills read as hills rather
    # than as valleys.
    lx, ly, lz = -0.5, 0.5, math.sqrt(0.5)
    metres = metres or PIXEL_METRES * (2048 / size)
    rows = bytearray()
    last = size - 1
    for j in range(size):
        rows.append(0)
        up, down = max(j - 1, 0) * size, min(j + 1, last) * size
        row = j * size
        below = min(j + 1, last) * size
        for i in range(size):
            h = hs[row + i]
            east = hs[row + min(i + 1, last)]
            west = hs[row + max(i - 1, 0)]
            dx = (east - west) / (2 * metres)
            dn = (hs[up + i] - hs[down + i]) / (2 * metres)
            nx, ny = -dx * RELIEF, -dn * RELIEF
            shade = (nx * lx + ny * ly + lz) / math.sqrt(nx * nx + ny * ny + 1.0)
            light = min(1.25, max(0.3, 0.3 + 0.7 * shade / lz))

            a = h - WATERLINE
            r = east - WATERLINE
            d = hs[below + i] - WATERLINE
            # The coast is drawn on the land, whichever side the sea is on,
            # so it looks to all four neighbours and not just two.
            wet = a >= 0.0 and min(west, hs[up + i], east, hs[below + i]) < WATERLINE

            if a < 0.0:
                colour = _ramp(SEA_RAMP, -a)
                light = 1.0 + (light - 1.0) * 0.3
            else:
                low, high = tints[biomes[row + i]]
                colour = _mix(low, high, a / HIGH)
            colour = tuple(min(255, int(c * light)) for c in colour)

            # A line where this pixel and the one east or south of it fall
            # either side of a contour. Drawn on one side only, so a line is
            # one pixel wide rather than two.
            if wet:
                colour = COAST
            elif (a >= 0.0) != (r >= 0.0) or (a >= 0.0) != (d >= 0.0):
                pass
            else:
                k = math.floor(a / CONTOUR)
                if k != math.floor(r / CONTOUR) or k != math.floor(d / CONTOUR):
                    heavy = (math.floor(a / INDEX_CONTOUR)
                             != math.floor(r / INDEX_CONTOUR)
                             or math.floor(a / INDEX_CONTOUR)
                             != math.floor(d / INDEX_CONTOUR))
                    slope = math.hypot(dx, dn)
                    spacing = CONTOUR / (slope * metres) if slope else 1e9
                    if a < 0.0:
                        dim = 0.88 if heavy else 0.94
                    elif heavy:
                        dim = 0.55
                    else:
                        # On a cliff the lines would run together into a
                        # dark smear; there only the heavy ones are kept.
                        dim = 0.75 if spacing >= 2.5 else 1.0
                    colour = tuple(int(c * dim) for c in colour)
            rows += bytes(colour)
    return bytes(rows)


def render(world, size=2048, progress=None):
    """The world as an RGB PNG, `size` pixels square.

    Slow -- about fifty thousand points a second, each one a dozen noise
    samples -- and worth caching for ever, because a seed's terrain cannot
    change. Valheim's own in-game map is 2048 square for this same area, so
    there is nothing to gain by going finer.
    """
    hs, biomes = heights(world, size, progress)
    return _png(topo_rows(hs, biomes, size), size)


def _png(rows, size):
    import struct
    import zlib

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6))
            + chunk(b"IEND", b""))
