"""The map is layers stacked on each other, and they must not block.

Every layer is a full-plate box drawn over the last. A box that takes
clicks hides every layer beneath it, so adding one on top quietly stops the
ones below from responding -- which is exactly what happened: corpses and
boats landed above the portals, and clicking a portal stopped drawing the
line to its other end. Nothing failed; the click simply never arrived.
"""
import re

from skald.app import MAP_PAGE


def _style():
    return re.search(r"<style>(.*?)</style>", MAP_PAGE, re.S).group(1)


def _rules():
    """(selector, body) for every rule in the map page's stylesheet."""
    css = re.sub(r"/\*.*?\*/", "", _style(), flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def test_no_full_plate_overlay_swallows_clicks():
    """A rule that covers the whole plate must let clicks through, unless it
    is the thing meant to receive them."""
    for selector, body in _rules():
        flat = body.replace(" ", "")
        covers = "position:absolute" in flat and "inset:0" in flat
        if not covers:
            continue
        if ".pin" in selector and ".pins" not in selector:
            continue           # a marker is meant to be clickable
        if "plate" in selector:
            continue           # the plate is the stack itself
        assert "pointer-events:none" in flat, (
            f"{selector.strip()} covers the plate and takes clicks, "
            "so every layer under it stops responding")


def test_the_markers_themselves_still_take_clicks():
    """The guard above must not be satisfied by making nothing clickable."""
    for selector, body in _rules():
        if selector.strip() == ".pin":
            assert "pointer-events:auto" in body.replace(" ", "")
            return
    raise AssertionError("no .pin rule found")
