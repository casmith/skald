"""Lines between the two ends of a portal.

The page is told which portals are the ends of one -- a key they share,
worked out from the links in the save (see test_portal_pairs) -- and draws
by that, not by tag. This file used to say the save held no link and that
the tag was exact because no tag was used more than twice. Both were wrong:
Jotunheim has four portals tagged XP1, which are two pairs, and a tap on
one drew lines to all three of the others.
"""
import json
import re
import shutil
import subprocess

import pytest

from skald.app import MAP_PAGE

pytestmark = pytest.mark.skipif(not shutil.which("node"),
                                reason="node is not installed")

HARNESS = """
let handler = null, drawn = '';
function mk() {
  return {style:{setProperty(){}, transform:''}, className:'', hidden:true,
    appendChild(){}, addEventListener(){}, toggleAttribute(){},
    querySelector: () => null,
    classList: {add(){}, remove(){}, toggle(){}, contains: () => false}, dataset:{},
    getBoundingClientRect: () => ({left:0,top:0,width:10,height:10,bottom:10}),
    clientWidth:600, offsetWidth:50, offsetHeight:20,
    classList:{add(){}, remove(){}, toggle(){}, contains(){return false;}},
    set innerHTML(v){}, get innerHTML(){ return ''; }};
}
const portalBox = mk(); portalBox.dataset.portals = JSON.stringify(%s);
let markers = '';
Object.defineProperty(portalBox, 'innerHTML',
  {set(v){ markers = v; }, get(){ return markers; }});
const links = mk();
Object.defineProperty(links, 'innerHTML', {set(v){ drawn = v; }, get(){ return drawn; }});
const viewer = mk(), plate = mk();
viewer.addEventListener = (kind, fn) => { if (kind === 'pointerup') handler = fn; };
viewer.setPointerCapture = () => {};
// The map sizes itself to the window it is in.
global.window = {innerHeight: 800, scrollY: 0, addEventListener() {}};
global.document = {
  getElementById: id => ({portals: portalBox, links: links, viewer: viewer,
                          plate: plate}[id] || mk()),
  querySelectorAll: () => [], createElement: () => mk(),
  elementFromPoint: () => ({dataset: {names: 'x', g: '%s'}, parentElement: null,
    getBoundingClientRect: () => ({left:0,top:0,width:10,height:10,bottom:10})}),
};
%s
handler({clientX: 5, clientY: 5});
console.log(JSON.stringify({lines: (drawn.match(/<line /g) || []).length, raw: drawn}));
"""


def _tap(portals, group, tmp_path):
    js = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S).group(1)
    path = tmp_path / "links.js"
    path.write_text(HARNESS % (json.dumps(portals), group, js))
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _p(name, x, y, k=None):
    out = {"n": name, "x": x, "y": y}
    if k is None:
        out["lone"] = True
    else:
        out["k"] = k
    return out


def test_tapping_one_end_draws_a_line_to_the_other(tmp_path):
    out = _tap([_p("Haldor", 10.0, 10.0, 0), _p("Haldor", 80.0, 80.0, 0)], 0,
               tmp_path)
    assert out["lines"] == 1
    assert 'x1="10.000"' in out["raw"] and 'x2="80.000"' in out["raw"]


def test_a_lone_end_draws_nothing(tmp_path):
    """Twenty-one of Warheimer's tags are used once -- the other end was
    destroyed or never built. There is no line to draw."""
    out = _tap([_p("BONER", 10.0, 10.0), _p("Elder", 80.0, 80.0)], 0, tmp_path)
    assert out["lines"] == 0


def test_four_portals_with_one_tag_are_two_pairs(tmp_path):
    """The case reported: four XP1s, and a tap on one drew a line to every
    other XP1. It goes to one place, so it gets one line."""
    portals = [_p("XP1", 10.0, 10.0, 0), _p("XP1", 80.0, 80.0, 1),
               _p("XP1", 20.0, 70.0, 0), _p("XP1", 70.0, 20.0, 1)]
    out = _tap(portals, 0, tmp_path)
    assert out["lines"] == 1
    assert 'x2="20.000"' in out["raw"] and 'y2="70.000"' in out["raw"]


def test_an_untagged_pair_is_still_a_pair(tmp_path):
    """The game pairs untagged portals with each other too, and the save
    says which."""
    out = _tap([_p("", 10.0, 10.0, 3), _p("", 80.0, 80.0, 3)], 0, tmp_path)
    assert out["lines"] == 1


def test_a_huddle_draws_a_line_for_every_tag_under_it(tmp_path):
    """Tapping a hub should reach everywhere that hub goes, not one place."""
    portals = [_p("A", 50.0, 50.0, 0), _p("B", 50.02, 50.02, 1),
               _p("A", 10.0, 90.0, 0), _p("B", 90.0, 10.0, 1)]
    out = _tap(portals, 0, tmp_path)
    assert out["lines"] == 2


def test_no_line_back_to_the_marker_itself(tmp_path):
    """Both ends inside one huddle is one marker; a line to itself is a
    dot, and worse, a lie about where it goes."""
    out = _tap([_p("A", 50.0, 50.0, 0), _p("A", 50.02, 50.02, 0)], 0, tmp_path)
    assert out["lines"] == 0
