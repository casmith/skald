"""The map's script, run.

The grouping was already tested on its own and passed while the map showed
no portals at all: the marker-building around it handed a portal object to
something expecting a string, threw, and stopped the drawing before a single
marker existed. A test of one function cannot see that. This runs the whole
script against a stub of the few DOM calls it makes, and checks markers come
out the other end.
"""
import html as H
import json
import re
import shutil
import subprocess

import pytest

from skald.app import MAP_PAGE

pytestmark = pytest.mark.skipif(not shutil.which("node"),
                                reason="node is not installed")

HARNESS = """
const portals = %s;
let built = null;
const box = {dataset: {portals: JSON.stringify(portals)},
             set innerHTML(v) { built = v; }, get innerHTML() { return built; }};
const stub = () => ({style: {setProperty(){}, transform: ''}, className: '',
  hidden: true, appendChild(){}, addEventListener(){}, toggleAttribute(){},
  getBoundingClientRect: () => ({left:0, top:0, width:10, height:10, bottom:10}),
  clientWidth: %d, offsetWidth: 50, offsetHeight: 20});
const viewer = stub(), plate = stub();
global.document = {
  getElementById: id => id === 'portals' ? box : (id === 'viewer' ? viewer : plate),
  querySelectorAll: () => [], createElement: () => stub()};
%s
if (built === null) { console.log('NONE'); }
else { console.log(built); }
"""


def _run(portals, tmp_path, width=600):
    js = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S).group(1)
    path = tmp_path / "run.js"
    path.write_text(HARNESS % (json.dumps(portals), width, js))
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = done.stdout.strip()
    assert out != "NONE", "the script drew no markers at all"
    return out


def _p(name, x, y, lone=False):
    return {"n": name, "x": x, "y": y, "lone": lone}


def test_markers_are_drawn(tmp_path):
    out = _run([_p("Bogwitch", 40.0, 40.0), _p("Elder", 80.0, 80.0)], tmp_path)
    assert out.count('class="pin portal') == 2


def test_a_lone_named_portal_gets_its_label(tmp_path):
    """The exact shape that threw: one portal, on its own, with a name. The
    label was handed the portal instead of its name."""
    out = _run([_p("Haldor", 20.0, 20.0), _p("Elder", 80.0, 80.0)], tmp_path)
    assert "<i>Haldor</i>" in out
    assert "[object" not in out


def test_a_huddle_is_one_marker_naming_everything(tmp_path):
    hub = [_p("Bogwitch", 50.0, 50.0), _p("Alpa", 50.05, 50.05),
           _p("Elder", 50.1, 49.95)]
    out = _run(hub, tmp_path)
    assert out.count('class="pin portal') == 1
    assert "<em>3</em>" in out
    names = re.search(r'data-names="([^"]*)"', out).group(1)
    assert sorted(H.unescape(names).split("\n")) == ["Alpa", "Bogwitch", "Elder"]


def test_unnamed_portals_are_counted_not_dropped(tmp_path):
    hub = [_p("Bogwitch", 50.0, 50.0), _p("", 50.05, 50.05), _p("", 50.1, 49.95)]
    out = _run(hub, tmp_path)
    names = H.unescape(re.search(r'data-names="([^"]*)"', out).group(1))
    assert "2 unnamed" in names
    assert "<em>3</em>" in out


def test_a_world_with_no_portals_does_not_break_the_page(tmp_path):
    js = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S).group(1)
    path = tmp_path / "empty.js"
    path.write_text(HARNESS % ("[]", 600, js))
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
