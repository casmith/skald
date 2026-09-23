"""The weather engine, against vectors from the reference implementation.

Valheim's weather is deterministic: one roll per 666 seconds of world time,
read by each biome through a weighted table, so a world's clock decides its
weather entirely. These vectors come from Jere Kuusela's valheim-weather
(public domain), which is the reference this engine was ported from.
"""
import json
from pathlib import Path

import pytest

from skald import weather

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "weather_vectors.json")
                     .read_text())["vectors"]


@pytest.mark.parametrize("v", VECTORS, ids=lambda v: f"t={v['time']}")
def test_matches_the_reference_implementation(v):
    report = weather.report(v["time"])
    assert {b["biome"]: b["weather"] for b in report["biomes"]} == v["biomes"]
    angle, intensity = weather.wind_at(v["time"])
    assert angle == pytest.approx(v["wind_angle"], abs=1e-9)
    assert intensity == pytest.approx(v["wind_intensity"], abs=1e-12)
    if v["clock"] != "Intro":  # the reference labels the opening storm
        assert report["clock"] == v["clock"]


def test_every_world_opens_on_the_same_storm():
    """The first three periods are scripted, whatever the biome."""
    for t in (0, 666, 1332):
        assert all(b["weather"] == "ThunderStorm" for b in weather.report(t)["biomes"])
    assert weather.report(1998)["biomes"][0]["weather"] != "ThunderStorm"


def test_the_day_is_thirty_minutes_long():
    assert weather.day_and_clock(0) == (0, "00:00")
    assert weather.day_and_clock(weather.DAY_LENGTH) == (1, "00:00")
    assert weather.day_and_clock(weather.DAY_LENGTH * 2.5)[1] == "12:00"


@pytest.mark.parametrize("clock,phase,nxt", [
    ("00:00", "night", "dawn"), ("03:36", "dawn", "day"),
    ("06:00", "day", "dusk"), ("18:00", "dusk", "night"),
    ("20:24", "night", "dawn"),
])
def test_the_day_night_phases(clock, phase, nxt):
    """A new day is announced at 03:36, light runs 06:00-18:00, night at 20:24."""
    hours, minutes = (int(x) for x in clock.split(":"))
    t = (hours * 3600 + minutes * 60) * weather.DAY_LENGTH / 86400
    assert weather.phase_at(t)[:2] == (phase, nxt)


def test_night_is_nine_minutes_of_the_thirty():
    """20:24 to 03:36 the next day, in world seconds."""
    night_start = weather.PHASES[-1][0] * weather.DAY_LENGTH
    assert weather.phase_at(night_start)[3] == 540


def test_a_forecast_covers_the_days_asked_for():
    rows = weather.forecast(0, days=7)
    span = 7 * weather.DAY_LENGTH
    assert rows[0]["now"] and not rows[-1]["now"]
    assert len(rows) == span // weather.WEATHER_PERIOD + 1
    assert all(set(r["biomes"]) == set(weather.BIOMES) for r in rows)


def test_the_forecast_starts_where_the_world_is_now():
    """The current period is reported from now, not from when it began."""
    t = 3 * weather.WEATHER_PERIOD + 100
    assert weather.forecast(t, days=1)[0]["world_time"] == t
