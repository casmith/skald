"""Portals that sit on top of each other are drawn as one.

A hub can hold six within twenty metres, and at the map's own scale a
marker covers a few hundred metres of ground -- so drawn separately they
are one illegible blob and the names are unreadable, which is how this was
reported.
"""
from skald import app


def _p(name, x, z):
    return {"name": name, "x": float(x), "y": 30.0, "z": float(z)}


def test_a_hub_becomes_one_group():
    hub = [_p("Bogwitch", -301, 241), _p("Alpa", -299, 242),
           _p("Elder", -292, 240), _p("Haldor", -291, 238)]
    groups = app.cluster_portals(hub)
    assert len(groups) == 1
    assert len(groups[0]) == 4


def test_portals_far_apart_stay_apart():
    far = [_p("Bogwitch", -301, 241), _p("Elder", 1513, 1690),
           _p("Haldor", 604, -2870)]
    assert len(app.cluster_portals(far)) == 3


def test_every_portal_lands_in_exactly_one_group():
    """The count on a marker has to be the truth, so nothing may be dropped
    or counted twice."""
    holes = [_p(f"p{i}", i * 150, 0) for i in range(12)]
    holes += [_p("far", 9000, -9000)]
    groups = app.cluster_portals(holes)
    got = [h["name"] for g in groups for h in g]
    assert sorted(got) == sorted(h["name"] for h in holes)
    assert len(got) == len(set(got))


def test_a_line_of_portals_does_not_chain_into_one_group():
    """Each joins the nearest group rather than the first it touches. By
    first-match a row of portals each within reach of the last becomes a
    single group spanning kilometres."""
    line = [_p(f"p{i}", i * 380, 0) for i in range(10)]
    groups = app.cluster_portals(line)
    assert len(groups) > 1
    widest = max(max(h["x"] for h in g) - min(h["x"] for h in g)
                 for g in groups)
    assert widest < 3000


def test_a_lone_portal_is_its_own_group():
    assert app.cluster_portals([_p("only", 0, 0)]) == [[_p("only", 0, 0)]]


def test_no_portals_no_groups():
    assert app.cluster_portals([]) == []
