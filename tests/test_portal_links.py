"""Lines between the two ends of a portal.

Valheim stores no link. A portal's record in the save holds a tag, a
creator and sometimes a health, and that is all -- there is no reference to
the other end, because the game pairs them by tag when it loads. So the tag
is the pairing, and on these worlds no tag is used more than twice, which
makes it exact rather than a guess.
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
    appendChild(){}, addEventListener(){}, toggleAttribute(){}, dataset:{},
    getBoundingClientRect: () => ({left:0,top:0,width:10,height:10,bottom:10}),
    clientWidth:600, offsetWidth:50, offsetHeight:20,
    classList:{toggle(){}, contains(){return false;}},
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


def _p(name, x, y):
    return {"n": name, "x": x, "y": y, "lone": False}


def test_tapping_one_end_draws_a_line_to_the_other(tmp_path):
    out = _tap([_p("Haldor", 10.0, 10.0), _p("Haldor", 80.0, 80.0)], 0, tmp_path)
    assert out["lines"] == 1
    assert 'x1="10.000"' in out["raw"] and 'x2="80.000"' in out["raw"]


def test_a_lone_end_draws_nothing(tmp_path):
    """Twenty-one of Warheimer's tags are used once -- the other end was
    destroyed or never built. There is no line to draw."""
    out = _tap([_p("BONER", 10.0, 10.0), _p("Elder", 80.0, 80.0)], 0, tmp_path)
    assert out["lines"] == 0


def test_a_huddle_draws_a_line_for_every_tag_under_it(tmp_path):
    """Tapping a hub should reach everywhere that hub goes, not one place."""
    portals = [_p("A", 50.0, 50.0), _p("B", 50.02, 50.02),
               _p("A", 10.0, 90.0), _p("B", 90.0, 10.0)]
    out = _tap(portals, 0, tmp_path)
    assert out["lines"] == 2


def test_no_line_back_to_the_marker_itself(tmp_path):
    """Both ends inside one huddle is one marker; a line to itself is a
    dot, and worse, a lie about where it goes."""
    out = _tap([_p("A", 50.0, 50.0), _p("A", 50.02, 50.02)], 0, tmp_path)
    assert out["lines"] == 0
