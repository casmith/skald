"""The map fits the window it is in.

It is sized to the room under the header. The first measurement is taken
while the page's web fonts are still loading, and the header grows when they
arrive -- on a real world by 25 px, which pushed the bottom of the map off
the screen it had just been sized to fit. So it measures again once the
fonts are in, and again on load and on resize.
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
const on = {};
let top = 185;                       // where the map starts, under the header
const fits = [];
const stub = () => ({style: {setProperty(){}, transform: ''}, className: '',
  hidden: true, appendChild(){}, addEventListener(){}, toggleAttribute(){},
  querySelector: () => null, setPointerCapture(){}, releasePointerCapture(){},
  classList: {add(){}, remove(){}, toggle(){}, contains: () => false},
  getBoundingClientRect: () => ({left:0, top:0, width:500, height:500, bottom:500}),
  clientWidth: 500, offsetWidth: 50, offsetHeight: 20,
  dataset: {}, set innerHTML(v) {}, get innerHTML() { return ''; }});
const plate = stub();
const viewer = stub();
viewer.getBoundingClientRect = () => ({left:0, top: top, width:500, height:500,
                                       bottom: top + 500});
viewer.style.setProperty = (k, v) => { if (k === '--fit') fits.push(v); };
let fontsLoaded;
global.window = {innerHeight: 720, scrollY: 0,
  addEventListener(name, fn) { (on[name] = on[name] || []).push(fn); }};
global.document = {
  getElementById: id => ({viewer: viewer, plate: plate}[id] || stub()),
  querySelectorAll: () => [], createElement: () => stub(),
  addEventListener(){},
  fonts: {ready: new Promise(r => { fontsLoaded = r; })}};
%s
(async () => {
  %s
  console.log(JSON.stringify(fits));
})();
"""


def _fits(body, tmp_path):
    js = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S).group(1)
    path = tmp_path / "fit.js"
    path.write_text(HARNESS % (js, body))
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def test_it_fits_the_room_under_the_header(tmp_path):
    """720 high, the map starting 185 down, 12 to spare below it."""
    assert _fits("", tmp_path)[0] == "523px"


def test_it_measures_again_once_the_fonts_are_in(tmp_path):
    """The case that shipped: fonts arrive, the header grows 25 px, and a
    map sized before then hangs 25 px off the bottom of the screen."""
    fits = _fits("""
      top = 210;
      fontsLoaded();
      await document.fonts.ready;
    """, tmp_path)
    assert fits[0] == "523px"
    assert fits[-1] == "498px"


def test_and_when_the_page_has_loaded(tmp_path):
    fits = _fits("""
      top = 200;
      on.load.forEach(fn => fn());
    """, tmp_path)
    assert fits[-1] == "508px"


def test_and_when_the_window_changes_size(tmp_path):
    fits = _fits("""
      window.innerHeight = 900;
      on.resize.forEach(fn => fn());
    """, tmp_path)
    assert fits[-1] == "703px"


def test_without_room_it_fills_the_screen(tmp_path):
    """A phone on its side: the header alone is half the screen."""
    fits = _fits("""
      window.innerHeight = 390; top = 211;
      on.resize.forEach(fn => fn());
    """, tmp_path)
    assert fits[-1] == "378px"
