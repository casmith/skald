"""The fog is on the map unless the server says otherwise.

Two separate things. The map must never appear without it, even for a
moment -- the terrain and the fog are separate pictures and whichever lands
first is what you see, so a cached terrain beside an uncached fog showed
the whole world. And whether anyone may take it off at all is the server
owner's decision, not a matter of what the page happens to link to.
"""
import re

import pytest

from skald import app, config


@pytest.fixture
def one_world(monkeypatch):
    monkeypatch.setattr(app, "WORLD_MAPS", {
        "Midgard": {"edge": 2048, "seen": 1000, "explored": b"", "pins": []}})
    for name in ("WORLD_PORTALS", "WORLD_BUILT", "WORLD_BOATS", "WORLD_CORPSES"):
        monkeypatch.setattr(app, name, {})
    monkeypatch.setattr(app, "terrain_png", lambda w: b"PNG")


def _page(**kw):
    return app.render_map("Midgard",
                          shared={"Midgard": {"edge": 2048, "seen": 1000}},
                          seeds={"Midgard": {"seed": 1, "seed_name": "x"}}, **kw)


def test_the_map_is_hidden_until_the_fog_is_on_it(one_world):
    """Not a transition or a fade: the plate does not show at all until the
    fog image has loaded."""
    page = _page()
    style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    m = re.search(r"\.plate\{([^}]*)\}", style)
    assert "visibility:hidden" in m.group(1).replace(" ", "")
    assert ".plate.ready{visibility:visible}" in style.replace(" ", "")
    assert "addEventListener('load', reveal)" in page


def test_it_shows_anyway_if_the_fog_never_arrives(one_world):
    """A map that never appears is worse than one that appears late."""
    page = _page()
    assert "addEventListener('error', reveal)" in page
    assert "setTimeout(reveal, 4000)" in page


def test_a_world_with_no_fog_still_shows(one_world, monkeypatch):
    """Nothing to wait for, so nothing waits."""
    page = _page()
    assert "if (!fogImg || fogImg.complete)" in page


def test_the_server_refuses_to_drop_the_fog_when_it_is_not_allowed(one_world, monkeypatch):
    """The query string is not a permission. Hiding the link would leave
    ?fog=0 working for anyone who typed it."""
    monkeypatch.setattr(app, "CONFIG",
                        config.load(path="/none", env={"SKALD_ALLOW_FOG_OFF": "0"}))
    page = _page(fog=False)
    assert 'class="fog"' in page, "the fog was dropped despite the setting"
    assert "fog of war</a>" not in page, "it still offered the link"


def test_it_is_allowed_by_default(one_world, monkeypatch):
    monkeypatch.setattr(app, "CONFIG", config.load(path="/none", env={}))
    assert 'class="fog"' not in _page(fog=False)
    assert "fog of war</a>" in _page(fog=True)
