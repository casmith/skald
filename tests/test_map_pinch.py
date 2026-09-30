"""Pinch to zoom.

The viewer sets touch-action:none so it can drive its own pan, which also
tells the browser not to pinch-zoom for us. Zooming was on the wheel alone,
and a phone has no wheel -- so on mobile the map was stuck at 1x with no way
out. These drive the real script's pointer handlers with two fingers.
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
const handlers = {};
const stub = () => ({style: {setProperty(){}, transform: ''}, className: '',
  hidden: true, appendChild(){}, addEventListener(){}, toggleAttribute(){},
  querySelector: () => null, setPointerCapture(){}, releasePointerCapture(){},
  classList: {add(){}, remove(){}, toggle(){}, contains: () => false},
  getBoundingClientRect: () => ({left:0, top:0, width:600, height:600, bottom:600}),
  clientWidth: 600, offsetWidth: 50, offsetHeight: 20,
  dataset: {}, set innerHTML(v) {}, get innerHTML() { return ''; }});
const plate = stub();
const viewer = stub();
viewer.addEventListener = (name, fn) => { handlers[name] = fn; };
const portalBox = stub(); portalBox.dataset.portals = '[]';
global.document = {
  getElementById: id => ({viewer: viewer, plate: plate, portals: portalBox}[id] || stub()),
  querySelectorAll: () => [], createElement: () => stub(),
  addEventListener(){}};
%s
const ev = (id, x, y) => ({pointerId: id, clientX: x, clientY: y,
                           preventDefault(){}});
%s
const m = /scale\\(([0-9.]+)\\)/.exec(plate.style.transform);
const t = /translate\\((-?[0-9.]+)px,(-?[0-9.]+)px\\)/.exec(plate.style.transform);
console.log(JSON.stringify({
  scale: m ? Number(m[1]) : null,
  x: t ? Number(t[1]) : null, y: t ? Number(t[2]) : null,
  transform: plate.style.transform}));
"""


def _run(body, tmp_path):
    js = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S).group(1)
    path = tmp_path / "run.js"
    path.write_text(HARNESS % (js, body))
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def test_two_fingers_spreading_zoom_in(tmp_path):
    """100px apart to 200px apart is a doubling, so 1x becomes 2x."""
    out = _run("""
      handlers.pointerdown(ev(1, 200, 300));
      handlers.pointerdown(ev(2, 300, 300));
      handlers.pointermove(ev(2, 400, 300));
    """, tmp_path)
    assert out["scale"] == pytest.approx(2.0, rel=0.02)


def test_two_fingers_closing_zoom_out(tmp_path):
    """Spread 100px to 400px apart is 4x; closing back to 200px halves it."""
    out = _run("""
      handlers.pointerdown(ev(1, 100, 300));
      handlers.pointerdown(ev(2, 200, 300));
      handlers.pointermove(ev(2, 500, 300));
      handlers.pointermove(ev(2, 300, 300));
    """, tmp_path)
    assert out["scale"] == pytest.approx(2.0, rel=0.02)


def test_it_cannot_be_pinched_below_1x(tmp_path):
    """The map has no business being smaller than its own frame."""
    out = _run("""
      handlers.pointerdown(ev(1, 100, 300));
      handlers.pointerdown(ev(2, 500, 300));
      handlers.pointermove(ev(2, 110, 300));
    """, tmp_path)
    assert out["scale"] == pytest.approx(1.0)


def test_a_pinch_that_drifts_pans_as_well(tmp_path):
    """Both fingers moving the same way is a pan, at whatever zoom."""
    out = _run("""
      handlers.pointerdown(ev(1, 200, 300));
      handlers.pointerdown(ev(2, 400, 300));
      handlers.pointermove(ev(2, 500, 300));   /* zoom in first */
      handlers.pointermove(ev(1, 100, 300));
      handlers.pointermove(ev(2, 400, 300));   /* both slid left */
    """, tmp_path)
    assert out["scale"] > 1.0
    assert out["x"] < 0


def test_lifting_one_finger_carries_on_panning(tmp_path):
    """Not from wherever that finger was last seen on its own -- which was
    the start of the pinch, and would jump the map."""
    out = _run("""
      handlers.pointerdown(ev(1, 200, 300));
      handlers.pointerdown(ev(2, 400, 300));
      handlers.pointermove(ev(2, 500, 300));
      handlers.pointerup(ev(2, 500, 300));
      const before = plate.style.transform;
      handlers.pointermove(ev(1, 190, 300));
      if (plate.style.transform === before) throw new Error('did not pan');
    """, tmp_path)
    assert out["scale"] > 1.0


def test_one_finger_still_drags(tmp_path):
    """The gesture that already worked, unbroken by the one being added."""
    out = _run("""
      handlers.pointerdown(ev(1, 300, 300));
      handlers.pointermove(ev(1, 250, 280));
    """, tmp_path)
    # at 1x there is nowhere to pan to, so it clamps to 0 rather than moving
    assert out["scale"] == pytest.approx(1.0)
    assert out["x"] == 0
