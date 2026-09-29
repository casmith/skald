"""How much of a world the group has actually seen.

The map is drawn as a square, but Valheim's world is a disc: the ground
stops 10,500 metres out and the corners of the picture are somewhere nobody
can sail to. Counting them made every figure about 1.7 times smaller than
the truth.
"""
import math

from skald import fch


def test_the_corners_are_not_counted():
    edge = 512
    reachable = fch.explorable_pixels(edge)
    assert reachable < edge * edge
    # the disc, as a fraction of the square the texture covers
    want = math.pi * fch.WORLD_EDGE ** 2 / (2 * fch.MAP_SPAN) ** 2
    assert abs(reachable / (edge * edge) - want) < 0.01


def test_it_scales_with_the_map_size():
    """The same world, drawn bigger, is the same fraction of it."""
    a = fch.explorable_pixels(256) / 256 ** 2
    b = fch.explorable_pixels(2048) / 2048 ** 2
    assert abs(a - b) < 0.005


def test_a_fully_explored_world_is_a_hundred_percent():
    """With the square as the denominator this could only ever reach 57%."""
    edge = 256
    reachable = fch.explorable_pixels(edge)
    assert min(100.0, reachable / reachable * 100) == 100.0


def test_the_figure_is_what_the_old_one_was_times_about_1_7():
    """Pinning the correction, so a change to the world's radius shows up
    here rather than quietly moving everybody's numbers."""
    edge = 2048
    seen = 154_818                       # a real world's explored pixels
    old = seen / edge ** 2 * 100
    new = seen / fch.explorable_pixels(edge) * 100
    assert 1.70 < new / old < 1.80
    assert round(new, 2) == 6.44


def test_nothing_reports_more_than_all_of_it():
    """Should a pixel outside the disc ever read as explored, the answer is
    still not 103%."""
    edge = 128
    seen = edge * edge                   # every pixel, corners included
    assert min(100.0, seen / fch.explorable_pixels(edge) * 100) == 100.0
