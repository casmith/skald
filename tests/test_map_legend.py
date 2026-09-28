"""The legend, exercised.

These used to be links. A link reloads the page, and the page comes back at
the top left at 1x -- so turning off a layer threw away the part of the map
you were looking at, which is most of the reason you were looking at it.

They are checkboxes now, and a checkbox does one thing: put a class on the
plate. Nothing is refetched and nothing is redrawn, so the view cannot move.
That is the property worth testing, and it is not visible from the markup.
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
let handler = null;
const classes = new Set();
const plate = mk(); const viewer = mk();
plate.classList = {
  toggle: (name, on) => { if (on === false) classes.delete(name); else classes.add(name); },
  contains: n => classes.has(n),
};
const legend = mk();
legend.addEventListener = (kind, fn) => { if (kind === 'change') handler = fn; };
function mk() {
  return {style: {setProperty(){}, transform: ''}, className: '', hidden: true,
    appendChild(){}, addEventListener(){}, toggleAttribute(){},
    getBoundingClientRect: () => ({left:0,top:0,width:10,height:10,bottom:10}),
    clientWidth: 600, offsetWidth: 50, offsetHeight: 20,
    dataset: {}, set innerHTML(v) {}, get innerHTML() { return ''; }};
}
const portalBox = mk(); portalBox.dataset.portals = '[]';
global.document = {
  getElementById: id => ({legend: legend, viewer: viewer, plate: plate,
                          portals: portalBox}[id] || mk()),
  querySelectorAll: () => [], createElement: () => mk()};
%s
const fired = %s;
if (!handler) { console.log(JSON.stringify({error: 'no change handler'})); }
else {
  fired.forEach(f => handler({target: {dataset: {layer: f.layer}, checked: f.checked}}));
  console.log(JSON.stringify({classes: [...classes]}));
}
"""


def _toggle(events, tmp_path):
    js = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S).group(1)
    path = tmp_path / "legend.js"
    path.write_text(HARNESS % (js, json.dumps(events)))
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    return set(out["classes"])


def test_unchecking_hides_that_layer_and_nothing_else(tmp_path):
    got = _toggle([{"layer": "mine", "checked": False}], tmp_path)
    assert got == {"off-mine"}


def test_checking_it_again_brings_it_back(tmp_path):
    got = _toggle([{"layer": "mine", "checked": False},
                   {"layer": "mine", "checked": True}], tmp_path)
    assert got == set()


def test_layers_are_independent(tmp_path):
    got = _toggle([{"layer": "boss", "checked": False},
                   {"layer": "portals", "checked": False},
                   {"layer": "built", "checked": False}], tmp_path)
    assert got == {"off-boss", "off-portals", "off-built"}


def test_every_legend_layer_has_a_rule_that_hides_it():
    """A box that does nothing is worse than no box. Each layer the legend
    can offer must have a stylesheet rule keyed on its class."""
    from skald.app import PIN_LAYERS
    style = MAP_PAGE
    for kind, _ in PIN_LAYERS:
        assert f".plate.off-{kind} .pin.{kind}" in style, kind
    assert ".plate.off-portals #portals" in style
    assert ".plate.off-built .built" in style
