#!/usr/bin/env python3
"""
Valheim's weather, computed rather than observed.

Weather is deterministic: every 666 seconds of *world* time the game rolls
one number from a generator seeded with that period's index, and each biome
turns the same roll into its own weather through a weighted table. The world
seed plays no part, so every world sees the same sequence -- only its clock
decides where in that sequence it is. Wind comes from four octaves of the
same generator, on 125-second periods.

Ported from Jere Kuusela's valheim-weather (github.com/JereKuusela/
valheim-weather, Unlicense/public domain), which is the reference for the
period lengths, the biome tables and Unity's xorshift128 quirks -- notably
that Random.value is built from the *high* bits and that Random.Range flips
it to 1 - value.

The clock this needs is the world's own elapsed seconds (the double in a
save's header), which only advances while someone is online.
"""

WEATHER_PERIOD = 666
WIND_PERIOD = 1000 / 8
DAY_LENGTH = 1800
# Every world opens on the same scripted thunderstorm, for three periods.
INTRO_PERIODS = 3
# The day/night cycle, as fractions of the 1800-second day: a new day is
# announced at 03:36 and night falls at 20:24, giving 21 minutes of light and
# 9 of dark; daylight proper runs 06:00-18:00, with dusk between.
PHASES = [(0.15, "dawn"), (0.25, "day"), (0.75, "dusk"), (0.85, "night")]

WIND = {  # weather -> (min, max) wind intensity
    "Clear": (0.1, 0.6), "Rain": (0.5, 1.0), "Misty": (0.1, 0.3),
    "ThunderStorm": (0.8, 1.0), "LightRain": (0.1, 0.6),
    "DeepForest Mist": (0.1, 0.6), "SwampRain": (0.1, 0.3),
    "SnowStorm": (0.8, 1.0), "Snow": (0.1, 0.6), "Heath clear": (0.4, 0.8),
    "Twilight Snowstorm": (0.7, 1.0), "Twilight Snow": (0.3, 0.6),
    "Twilight Clear": (0.2, 0.6), "Ashrain": (0.1, 0.5),
    "Darklands dark": (0.1, 0.6),
}

INTRO = [(1.0, "ThunderStorm")]
BIOMES = {  # in progression order, as the page shows them
    "Meadows": [(5.0, "Clear"), (0.2, "Rain"), (0.2, "Misty"),
                (0.2, "ThunderStorm"), (0.2, "LightRain")],
    "Black Forest": [(2.0, "DeepForest Mist"), (0.1, "Rain"), (0.1, "Misty"),
                     (0.1, "ThunderStorm")],
    "Swamp": [(1.0, "SwampRain")],
    "Mountain": [(1.0, "SnowStorm"), (5.0, "Snow")],
    "Plains": [(2.0, "Heath clear"), (0.4, "Misty"), (0.4, "LightRain")],
    "Mistlands": [(1.0, "Darklands dark")],
    "Ashlands": [(1.0, "Ashrain")],
    "Deep North": [(0.5, "Twilight Snowstorm"), (1.0, "Twilight Snow"),
                   (1.0, "Twilight Clear")],
    "Ocean": [(0.1, "Rain"), (0.1, "LightRain"), (0.1, "Misty"),
              (1.0, "Clear"), (0.1, "ThunderStorm")],
}

# Plain English for the game's internal names.
PRETTY = {
    "Clear": "clear", "Rain": "rain", "Misty": "misty",
    "ThunderStorm": "thunderstorm", "LightRain": "light rain",
    "DeepForest Mist": "forest mist", "SwampRain": "swamp rain",
    "SnowStorm": "snowstorm", "Snow": "snow", "Heath clear": "clear",
    "Twilight Snowstorm": "twilight snowstorm", "Twilight Snow": "twilight snow",
    "Twilight Clear": "twilight, clear", "Ashrain": "ash rain",
    "Darklands dark": "dark mist",
}

MASK = 0xFFFFFFFF


class Rng:
    """Unity's Random, seeded as Random.InitState(seed) does."""

    def __init__(self, seed):
        a = seed & MASK
        b = (a * 1812433253 + 1) & MASK
        c = (b * 1812433253 + 1) & MASK
        d = (c * 1812433253 + 1) & MASK
        self.s = [a, b, c, d]

    def _next(self):
        a, b, c, d = self.s
        t1 = (a ^ (a << 11)) & MASK
        t2 = t1 ^ (t1 >> 8)
        nxt = (d ^ (d >> 19) ^ t2) & MASK
        self.s = [b, c, d, nxt]
        return nxt

    def value(self):
        """Random.value: the state's high bits, as Unity takes them."""
        return ((self._next() << 9) & MASK) / 4294967295

    def range(self):
        """Random.Range(0, 1), which Unity computes as 1 - value."""
        return 1.0 - self.value()


def weather_at(period, table):
    """The weather a biome's table gives for one weather period."""
    if period < INTRO_PERIODS:
        table = INTRO
    roll = Rng(period).range()
    total = sum(w for w, _ in table)
    target, run = total * roll, 0.0
    for weight, name in table:
        run += weight
        if target < run:
            return name
    return table[-1][1]


def wind_at(time):
    """Global wind at a world time: (direction it blows toward, strength)."""
    angle, intensity = 0.0, 0.5
    for octave in (1, 2, 4, 8):
        rng = Rng(int(time // (WIND_PERIOD * 8 / octave)))
        angle += rng.value() * 2 * 3.141592653589793 / octave
        intensity += (rng.value() - 0.5) / octave
    intensity = min(1.0, max(0.0, intensity))
    angle = angle * 180 / 3.141592653589793
    while angle > 180:
        angle -= 360
    return angle, intensity


COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(angle):
    return COMPASS[int((angle % 360 + 22.5) // 45) % 8]


def phase_at(time):
    """(phase now, what follows, its clock time, world seconds until it)."""
    f = (time % DAY_LENGTH) / DAY_LENGTH
    starts = [x for x, _ in PHASES]
    i = max((n for n, x in enumerate(starts) if f >= x), default=-1)
    now = PHASES[i][1] if i >= 0 else PHASES[-1][1]   # before 03:36 it's night
    nxt_at, nxt = PHASES[(i + 1) % len(PHASES)]
    until = (nxt_at - f) % 1.0 * DAY_LENGTH
    _, clock = day_and_clock(nxt_at * DAY_LENGTH)
    return now, nxt, clock, round(until)


def day_and_clock(time):
    """In-game day number and the time of day as HH:MM."""
    day = int(time // DAY_LENGTH)
    secs = (time % DAY_LENGTH) * 24 * 3600 / DAY_LENGTH
    return day, f"{int(secs // 3600):02d}:{int(secs % 3600 // 60):02d}"


# The biomes whose table holds only one weather never change, so a forecast
# grid would just repeat them.
VARYING = [b for b, table in BIOMES.items() if len(table) > 1]
CONSTANT = [b for b, table in BIOMES.items() if len(table) == 1]


def forecast(time, days=7):
    """Every weather period over the next `days` in-game days.

    In-game days are 1800 world seconds, weather periods 666, so a week is
    about 19 periods -- and, since the clock only runs while someone plays,
    3.5 hours of play rather than a week of waiting.
    """
    period = int(time // WEATHER_PERIOD)
    end = time + days * DAY_LENGTH
    rows = []
    for p in range(period, int(end // WEATHER_PERIOD) + 1):
        at = max(p * WEATHER_PERIOD, time)  # the current period starts "now"
        angle, intensity = wind_at(at)
        day, clock = day_and_clock(at)
        row = {"period": p, "world_time": round(at, 1), "day": day,
               "clock": clock, "now": p == period, "phase": phase_at(at)[0],
               "wind_from": compass(angle + 180),
               "wind": round(intensity, 2), "biomes": {}}
        for biome in BIOMES:
            name = weather_at(p, BIOMES[biome])
            row["biomes"][biome] = {"name": name, "label": PRETTY.get(name, name)}
        rows.append(row)
    return rows


def report(time):
    """Everything the page shows for one world, at one world time."""
    period = int(time // WEATHER_PERIOD)
    angle, intensity = wind_at(time)
    day, clock = day_and_clock(time)
    biomes = []
    for biome, table in BIOMES.items():
        name = weather_at(period, table)
        lo, hi = WIND.get(name, (0.0, 1.0))
        biomes.append({
            "biome": biome, "weather": name, "label": PRETTY.get(name, name),
            # Each weather clamps the global wind into its own range.
            "wind": round(lo + (hi - lo) * intensity, 2),
            "next": PRETTY.get(weather_at(period + 1, table),
                               weather_at(period + 1, table)),
        })
    phase, next_phase, next_phase_at, next_phase_in = phase_at(time)
    return {
        "world_time": round(time, 1), "day": day, "clock": clock,
        "phase": phase, "next_phase": next_phase,
        "next_phase_at": next_phase_at, "next_phase_in": next_phase_in,
        "period": period,
        "changes_in": round((period + 1) * WEATHER_PERIOD - time),
        "wind_from": compass(angle + 180), "wind_angle": round(angle, 1),
        "biomes": biomes,
    }
