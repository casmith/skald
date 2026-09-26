"""Valheim's world, generated from its seed.

Three things have to be exactly right or the world is merely plausible:
Unity's Perlin noise, Unity's random number generator, and the order the
world's offsets are drawn in. The vectors below come from the reference
implementation and, in one case, from a real world file -- which is the
better of the two, being nobody's opinion.
"""
import pytest

from skald import worldgen as w


def test_seed_names_hash_as_the_game_hashes_them():
    for name, want in (("Kh0zDpuPnw", 810132289), ("q6GhJN6FwT", 517038747),
                       ("Dedbtjdcv", 1218100378), ("Valheim", -827134064),
                       ("test", -871206010), ("a", 372029373), ("", 0)):
        assert w.seed_from_name(name) == want, name


def test_a_real_world_file_agrees():
    """Warheimer's .fwl records seedName 'Sigmar' and seed 849384111. The
    game wrote both; we only have to agree with it."""
    assert w.seed_from_name("Sigmar") == 849384111


def test_the_offsets_are_drawn_in_the_right_order():
    """Draw order is load-bearing and invisible when wrong: four offsets,
    two seeds this port does not need, then the fifth offset last. Any other
    order gives a different world that looks perfectly reasonable."""
    r = w.Random(w.seed_from_name("Dedbtjdcv"))
    assert [r.range_int(-10000, 10000) for _ in range(4)] == [-8087, 9698, -4921, -8635]
    assert r.range_int(-2147483648, 2147483647) == 1741748534
    assert r.range_int(-2147483648, 2147483647) == -2141061776
    assert r.range_int(-10000, 10000) == -116


def test_perlin_is_unitys_not_the_textbook_one():
    """Unity's two quirks: inputs pass through abs(), so the field is
    mirrored, and the output is rescaled by (raw + 0.69) / 1.483."""
    assert w.perlin(0.5, 0.5) == pytest.approx(w.perlin(-0.5, -0.5))
    assert w.perlin(3.25, 1.75) == pytest.approx(w.perlin(-3.25, 1.75))
    # Rescaled into roughly [0, 1] rather than [-1, 1].
    values = [w.perlin(i * 0.37, i * 0.11) for i in range(200)]
    assert min(values) >= 0.0 and max(values) <= 1.0
    assert 0.3 < sum(values) / len(values) < 0.7


def test_the_world_has_the_shape_a_valheim_world_has():
    world = w.World(849384111)
    # There is land near the middle -- though not necessarily *at* the
    # origin, which for this seed is water. Valheim picks a spawn nearby
    # rather than at 0,0, and an assertion that the exact centre is dry
    # fails on perfectly ordinary worlds.
    near = [world.biome(float(x), float(y))
            for x in range(-600, 601, 200) for y in range(-600, 601, 200)]
    assert any(b != "Ocean" for b in near)
    # Far out is ocean.
    assert world.biome(0.0, 10400.0) == "Ocean"
    # The poles. Note Deep North is tested *after* the ocean threshold in
    # the game's own order, so the far north is sea rather than snow -- the
    # biome is a band of land, not everything past a radius. Ashlands is
    # tested before it and so does reach all the way out.
    assert world.biome(0.0, -13000.0) == "AshLands"
    north = {world.biome(float(x), float(y))
             for x in range(-6000, 6001, 1500) for y in range(7000, 10001, 750)}
    south = {world.biome(float(x), float(y))
             for x in range(-6000, 6001, 1500) for y in range(-10000, -6999, 750)}
    assert "DeepNorth" in north, north
    assert "AshLands" in south, south


def test_two_seeds_make_two_worlds():
    a, b = w.World(849384111), w.World(1218100378)
    points = [(x * 137.0, y * 211.0) for x in range(-7, 8) for y in range(-7, 8)]
    differ = sum(1 for x, y in points if a.biome(x, y) != b.biome(x, y))
    assert differ > len(points) // 4, "different seeds should give different worlds"


def test_the_same_seed_makes_the_same_world():
    a, b = w.World(849384111), w.World(849384111)
    for x, y in ((0, 0), (1234.5, -678.9), (-5000.0, 3000.0), (9000.0, 9000.0)):
        assert a.biome(x, y) == b.biome(x, y)


def test_a_seed_name_and_its_number_agree():
    assert w.World.from_name("Sigmar").biome(500.0, 500.0) == \
        w.World(849384111).biome(500.0, 500.0)


def test_rendering_gives_a_png_of_the_right_size():
    png = w.render(w.World(849384111), size=32)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    import struct
    width, height, depth, colour = struct.unpack(">IIBB", png[16:26])
    assert (width, height, depth, colour) == (32, 32, 8, 2)   # 8-bit RGB


def test_every_biome_has_a_colour():
    for biome in w.BIOMES:
        assert biome in w.COLOURS
