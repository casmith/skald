"""Rivers, carved into the ground after the noise has shaped it.

They are not in the noise at all: the game lays them out once per world
from two seeds of their own, joining low ground with channels sixty to a
hundred metres wide. Without them every river on the map is dry land.
"""
from skald import worldgen as w


def test_unitys_generator_gives_unitys_numbers():
    """Seed 1234, captured from Unity by the reference implementation."""
    r = w.Random(1234)
    assert r.s == [1234, 3159640283, 3392860520, 3460949513]
    assert [r.next() for _ in range(5)] == [
        3463400838, 3496203776, 3452947669, 1278673611, 4169168310]


def test_a_float_range_rounds_as_the_native_code_does():
    """(1 - f) * max + f * min in single precision, so Range(20, 20) is
    not always 20 -- which the game's stream widths inherit."""
    r = w.Random(7)
    seen = {r.range_float(20.0, 20.0) for _ in range(400)}
    assert 20.0 in seen and any(v > 20.0 for v in seen)
    assert all(abs(v - 20.0) < 1e-5 for v in seen)
    r = w.Random(7)
    assert all(60.0 <= r.range_float(60.0, 100.0) <= 100.0 for _ in range(400))


def _one_river(width):
    rivers = w.Rivers()
    for k in range(40):
        rivers.add(k * 8.0, 0.0, width)
    return rivers


def test_the_middle_of_a_river_is_under_the_sea():
    rivers = _one_river(80.0)
    h = rivers.carve(160.0, 0.0, 0.3)           # 60 m of ground, on the line
    assert h * w.HEIGHT_MULTIPLIER < w.SEA_LEVEL
    # Wider rivers run deeper.
    assert h < _one_river(30.0).carve(160.0, 0.0, 0.3)


def test_a_river_leaves_alone_what_is_off_it_or_already_low():
    rivers = _one_river(80.0)
    assert rivers.carve(160.0, 200.0, 0.3) == 0.3        # past the bank
    assert rivers.carve(160.0, 0.0, 0.05) == 0.05        # already sea floor
    # The bank slopes rather than stepping.
    near, far = rivers.carve(160.0, 20.0, 0.3), rivers.carve(160.0, 60.0, 0.3)
    assert near < far < 0.3


def test_a_world_has_rivers_cut_through_its_land():
    """Somewhere the ground would stand dry without the river and is under
    water with it -- the bug was a river drawn as land."""
    world = w.World(849384111)
    dry = w.World(849384111, rivers=False)
    assert world.rivers.grid
    cut = 0
    for gx, gy in list(world.rivers.grid)[::50]:
        x, y = gx * 64.0, gy * 64.0
        if x * x + y * y > 9000.0 ** 2:
            continue
        biome, base = world.sample(x, y)
        if biome == "Ocean":
            continue
        if (dry.height(x, y, biome, base) > w.SEA_LEVEL
                > world.height(x, y, biome, base)):
            cut += 1
    assert cut > 10, cut
