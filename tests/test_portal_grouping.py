"""Markers that stand together, worked out by the page rather than here.

Where one marker stops and the next begins depends on how far in you are:
two portals twenty metres apart are four screen pixels apart at 2.5x and a
comfortable gap at 16x. So the grouping cannot be decided when the page is
built, and it lives in the script -- which is what these tests reach into.

The first attempt did decide it on the server, at a fixed distance in
metres, and swapped to ungrouped markers past a zoom threshold. Past that
threshold a hub was still a single blob, but every marker under it now knew
only its own name, so tapping one told you about one portal. That is the
bug these tests exist to keep fixed.
"""
import json
import re
import shutil
import subprocess

import pytest

from skald.app import MAP_PAGE

pytestmark = pytest.mark.skipif(not shutil.which("node"),
                                reason="node is not installed")


def _grouper():
    """The real `groupPins` out of the page, not a copy of it."""
    js = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S).group(1)
    m = re.search(r"\n function groupPins\(items, reach\) \{.*?\n \}", js, re.S)
    assert m, "groupPins is not in the page any more"
    return m.group(0)


def _group(items, reach, tmp_path):
    path = tmp_path / "g.js"
    path.write_text(
        _grouper()
        + "\nconst out = groupPins(" + json.dumps(items) + ", " + str(reach) + ");"
        + "\nconsole.log(JSON.stringify(out.map(g => g.all.map(p => p.n))));")
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _p(name, x, y):
    return {"n": name, "x": x, "y": y}


def test_a_hub_is_one_marker(tmp_path):
    hub = [_p("Bogwitch", 50.0, 50.0), _p("Alpa", 50.01, 50.01),
           _p("Elder", 50.02, 49.99), _p("Haldor", 49.99, 50.02)]
    groups = _group(hub, 0.5, tmp_path)
    assert len(groups) == 1
    assert sorted(groups[0]) == ["Alpa", "Bogwitch", "Elder", "Haldor"]


def test_zooming_in_splits_them(tmp_path):
    """The whole point: the same portals, a smaller reach, more markers."""
    hub = [_p("a", 50.0, 50.0), _p("b", 50.4, 50.0), _p("c", 53.0, 50.0)]
    assert len(_group(hub, 1.0, tmp_path)) == 2
    assert len(_group(hub, 0.1, tmp_path)) == 3


def test_every_pin_lands_in_exactly_one_group(tmp_path):
    """The count on a marker has to be the truth."""
    items = [_p(f"p{i}", 40 + i * 0.3, 50.0) for i in range(12)]
    groups = _group(items, 0.5, tmp_path)
    flat = [n for g in groups for n in g]
    assert sorted(flat) == sorted(p["n"] for p in items)
    assert len(flat) == len(set(flat))


def test_a_line_does_not_chain_into_one_group(tmp_path):
    """Nearest, not first. By first match a row of pins each within reach of
    the last becomes one group spanning the map."""
    items = [_p(f"p{i}", i * 0.9, 50.0) for i in range(10)]
    groups = _group(items, 1.0, tmp_path)
    assert len(groups) > 1


def test_nothing_groups_into_nothing(tmp_path):
    assert _group([], 1.0, tmp_path) == []
