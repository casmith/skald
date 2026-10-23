"""Pins that mark portals, folded into the portals they mark.

Some groups drop a pin on every portal they build, always the same icon.
Drawn on its own, that pin is a second mark a few metres from the portal's,
and tapping it says nothing about where the portal goes. So when the person
running Skald says which icon means "portal", a pin of that kind standing on
one becomes part of it. Nobody else's pins are touched: which icon means
"portal" is one group's habit, not something the game says.
"""
import json
import re
import shutil
import subprocess

import pytest

from skald import app, config


def _pin(name, x, z, type=6):
    return {"name": name, "x": x, "z": z, "type": type, "crossed": False}


def _portal(name, x, z):
    return {"name": name, "x": x, "y": 30.0, "z": z}


# --- the setting ----------------------------------------------------------

def test_off_unless_set():
    assert config.load(path="/nonexistent", env={}).portal_pins == ()


def test_the_environment_takes_a_comma_separated_list():
    cfg = config.load(path="/nonexistent",
                      env={"SKALD_PORTAL_PINS": " Bed, star ,"})
    assert cfg.portal_pins == ("bed", "star")
    assert cfg.sources["portal_pins"] == "$SKALD_PORTAL_PINS"


def test_the_file_takes_a_list(tmp_path):
    path = tmp_path / "skald.toml"
    path.write_text('portal_pins = ["bed"]\n')
    assert config.load(path=str(path), env={}).portal_pins == ("bed",)


# --- the folding ----------------------------------------------------------

def test_a_pin_on_a_portal_is_given_to_it():
    pins = [_pin("Kaunaz", 100.0, 100.0), _pin("camp", 100.0, 100.0, type=3)]
    kept, names = app.fold_portal_pins(
        pins, [_portal("Kaunaz", 100.5, 100.4)], ("bed",))
    assert [p["name"] for p in kept] == ["camp"]
    assert names == {0: ["Kaunaz"]}


def test_a_pin_too_far_from_any_portal_stays_a_pin():
    """The portal may not be generated yet, or may have been torn down; the
    mark is still somebody's."""
    pins = [_pin("Kaunaz", 100.0, 100.0)]
    kept, names = app.fold_portal_pins(
        pins, [_portal("Kaunaz", 106.0, 100.0)], ("bed",))
    assert kept == pins and not names


def test_nothing_is_folded_with_the_setting_off():
    pins = [_pin("Kaunaz", 100.0, 100.0)]
    kept, names = app.fold_portal_pins(
        pins, [_portal("Kaunaz", 100.0, 100.0)], ())
    assert kept == pins and not names


def test_at_a_hub_the_portal_with_the_pins_name_wins():
    """Jotunheim's Trondheim pin is 2.4 m from its portal and 5.6 m from
    the Kaunaz one beside it. Closer still, the name decides."""
    portals = [_portal("Kaunaz", 101.0, 100.0), _portal("Trondheim", 103.0, 100.0)]
    _, names = app.fold_portal_pins(
        [_pin("Trondheim", 100.0, 100.0)], portals, ("bed",))
    assert names == {1: ["Trondheim"]}


def test_otherwise_the_nearest_portal_takes_it():
    portals = [_portal("A", 104.0, 100.0), _portal("B", 101.0, 100.0)]
    _, names = app.fold_portal_pins(
        [_pin("Kaunaz", 100.0, 100.0)], portals, ("bed",))
    assert names == {1: ["Kaunaz"]}


def test_an_unnamed_pin_is_folded_but_says_nothing():
    kept, names = app.fold_portal_pins(
        [_pin("", 100.0, 100.0)], [_portal("Kaunaz", 100.0, 100.0)], ("bed",))
    assert kept == [] and not names


# --- the page -------------------------------------------------------------

def _page(pins, portals, kinds, monkeypatch):
    monkeypatch.setattr(app, "WORLD_MAPS", {
        "Midgard": {"edge": 2048, "seen": 1000, "explored": b"",
                    "pins": pins}})
    monkeypatch.setattr(app, "WORLD_PORTALS", {"Midgard": portals})
    for name in ("WORLD_BUILT", "WORLD_BOATS", "WORLD_CORPSES"):
        monkeypatch.setattr(app, name, {"Midgard": []})
    monkeypatch.setattr(app, "terrain_png", lambda w: b"PNG")
    monkeypatch.setattr(app, "CONFIG",
                        config.replace(app.CONFIG, portal_pins=kinds))
    return app.render_map("Midgard",
                          shared={"Midgard": {"edge": 2048, "seen": 1000}},
                          seeds={"Midgard": {"seed": 1, "seed_name": "x"}})


def _portal_data(page):
    import html
    raw = re.search(r'data-portals="([^"]*)"', page).group(1)
    return json.loads(html.unescape(raw))


def test_the_page_draws_the_pin_as_part_of_the_portal(monkeypatch):
    page = _page([_pin("Kaunaz", 100.0, 100.0)],
                 [_portal("Trondheim", 101.0, 100.0)], ("bed",), monkeypatch)
    assert 'class="pin bed' not in page
    assert _portal_data(page)[0]["p"] == "Kaunaz"
    # and the legend no longer counts it as a bed
    assert 'data-layer="bed"' not in page


def test_a_pin_named_as_its_portal_adds_nothing(monkeypatch):
    page = _page([_pin("Kaunaz", 100.0, 100.0)],
                 [_portal("Kaunaz", 101.0, 100.0)], ("bed",), monkeypatch)
    assert "p" not in _portal_data(page)[0]


def test_with_the_setting_off_the_page_is_as_it_was(monkeypatch):
    page = _page([_pin("Kaunaz", 100.0, 100.0)],
                 [_portal("Trondheim", 101.0, 100.0)], (), monkeypatch)
    assert 'class="pin bed' in page
    assert 'data-layer="bed"' in page
    assert "p" not in _portal_data(page)[0]


# --- what a tap says ------------------------------------------------------

HARNESS = """
function mk() {
  return {style:{setProperty(){}, transform:''}, className:'', hidden:true,
    appendChild(){}, addEventListener(){}, toggleAttribute(){},
    querySelector: () => null, dataset:{},
    getBoundingClientRect: () => ({left:0,top:0,width:10,height:10,bottom:10}),
    clientWidth:600, offsetWidth:50, offsetHeight:20,
    classList:{add(){}, remove(){}, toggle(){}, contains(){return false;}},
    set innerHTML(v){}, get innerHTML(){ return ''; }};
}
const portalBox = mk(); portalBox.dataset.portals = JSON.stringify(%s);
let markers = '';
Object.defineProperty(portalBox, 'innerHTML',
  {set(v){ markers = v; }, get(){ return markers; }});
const viewer = mk(), plate = mk();
viewer.setPointerCapture = () => {};
global.document = {
  getElementById: id => ({portals: portalBox, viewer: viewer,
                          plate: plate}[id] || mk()),
  querySelectorAll: () => [], createElement: () => mk(),
};
%s
console.log(JSON.stringify(markers));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("mark,said", [
    ({"n": "Trondheim", "p": "Kaunaz"}, "Trondheim · Kaunaz"),
    ({"n": "", "p": "Kaunaz"}, "Kaunaz"),
    ({"n": "Trondheim"}, "Trondheim"),
])
def test_a_tap_names_the_portal_and_its_pin(mark, said, tmp_path):
    js = re.search(r"<script>(.*?)</script>", app.MAP_PAGE, re.S).group(1)
    path = tmp_path / "marks.js"
    path.write_text(HARNESS % (json.dumps([dict(x=50.0, y=50.0, lone=False,
                                                **mark)]), js))
    done = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    markers = json.loads(done.stdout)
    assert f'data-names="{said}"' in markers
    assert f"<i>{said}</i>" in markers
