"""Which portal leads to which.

The game pairs portals by tag, but once: every few seconds the server joins
each portal with no partner to a random free one with the same tag, and
that pair is then fixed and saved with both ends. So the save, not the tag,
says where a portal goes -- four portals tagged XP1 are two pairs.

The record shapes here are made up, but in the layout a real world's chunks
use: flags, position, prefab hash, a two-byte rotation, then the connection
as a type byte and a number both ends share.
"""
import struct

from skald import app, fch


def _record(name, x, z, link=None, kind=fch.CONNECTION_PORTAL, rotation=True):
    flags = 0x0970 | (fch.FLAG_ROTATION if rotation else 0)
    if link is not None:
        flags |= fch.FLAG_CONNECTION
    out = struct.pack("<H3fi", flags, x, 35.0, z, fch.PORTAL_PREFAB)
    if rotation:
        out += b"\x1c\x82"
    if link is not None:
        out += struct.pack("<BI", kind, link)
    out += b"\x01" + struct.pack("<ii", 0x2a, 0)          # an int
    raw = name.encode()
    out += b"\x01" + struct.pack("<i", fch.PORTAL_TAG_KEY) + bytes([len(raw)]) + raw
    return out


def _read(tmp_path, *records):
    path = tmp_path / "00_01__0_1.chunk"
    path.write_bytes(b"\x00" * 6 + b"".join(records))
    return fch._portals_in(str(path))


def test_each_end_carries_the_number_it_shares_with_the_other(tmp_path):
    found = _read(tmp_path,
                  _record("XP1", 4596.0, -2571.0, link=0x94928995),
                  _record("XP1", -314.0, 319.0, link=0x94928995,
                          kind=fch.CONNECTION_PORTAL | fch.CONNECTION_TARGET),
                  _record("XP1", 1182.0, -2732.0, link=0x949289AB, rotation=False))
    assert [(p["name"], round(p["x"]), p["link"]) for p in found] == [
        ("XP1", 4596, 0x94928995), ("XP1", -314, 0x94928995),
        ("XP1", 1182, 0x949289AB)]


def test_an_unconnected_portal_has_no_link(tmp_path):
    found = _read(tmp_path, _record("new", 10.0, 10.0))
    assert found[0]["link"] is None and found[0]["name"] == "new"


def test_a_connection_of_another_kind_is_not_believed(tmp_path):
    """Not a portal connection means not the shape we know; the tag is still
    there to fall back on, and a wrong line is worse than a guessed one."""
    found = _read(tmp_path, _record("odd", 10.0, 10.0, link=7, kind=0x03))
    assert found[0]["link"] is None


def _p(name, link=None):
    return {"name": name, "x": 0.0, "z": 0.0, "link": link}


def test_the_save_decides_not_the_tag():
    keys = app.portal_pairs([_p("XP1", 1), _p("XP1", 2), _p("XP1", 1), _p("XP1", 2)])
    assert keys[0] == keys[2] and keys[1] == keys[3] and keys[0] != keys[1]


def test_untagged_portals_pair_by_link():
    keys = app.portal_pairs([_p("", 5), _p("", 5), _p("", None), _p("", None)])
    assert keys[0] == keys[1] is not None
    # without a link, which untagged portal goes where is anyone's guess
    assert keys[2] is None and keys[3] is None


def test_portals_too_new_to_be_paired_go_by_tag():
    """Built in the last few seconds: the game is about to pair them by tag,
    so the map does too."""
    keys = app.portal_pairs([_p("Haldor"), _p("Haldor"), _p("XP1", 9), _p("XP1", 9)])
    assert keys[0] == keys[1] is not None
    assert keys[2] == keys[3] != keys[0]


def test_a_link_whose_other_end_was_not_read_falls_back_to_the_tag():
    keys = app.portal_pairs([_p("Elder", 4), _p("Elder", None)])
    assert keys[0] == keys[1] is not None


def test_a_tag_nobody_else_carries_leads_nowhere():
    assert app.portal_pairs([_p("BONER"), _p("Elder", 1), _p("Elder", 1)])[0] is None
