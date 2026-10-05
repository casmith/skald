"""Sunken crypts, found by the gate across each entrance.

The location itself is not an object in the save, but its gate is, and it
stands on the swamp floor -- while the crypt's inside is built five thousand
metres up, like every other dungeon's.
"""
import re
import struct

from skald import fch
from skald.worldgen import stable_hash


def _gate(x, z, y=28.0):
    return (struct.pack("<3f", x, y, z)
            + struct.pack("<i", stable_hash(fch.CRYPT_GATE)) + b"\x07\x08")


def _needle():
    return struct.pack("<i", stable_hash(fch.CRYPT_GATE))


def test_crypts_are_read_where_their_gates_stand(tmp_path):
    path = tmp_path / "a.chunk"
    path.write_bytes(_gate(-1830.0, 2410.0) + b"\x00" * 40
                     + _gate(-2105.0, 2290.0, 33.5))
    got = sorted(fch._crypts_in(str(path), _needle()))
    assert [(round(x), round(z)) for x, z in got] == [(-2105, 2290),
                                                      (-1830, 2410)]


def test_a_gate_up_where_the_insides_are_built_is_not_an_entrance(tmp_path):
    path = tmp_path / "b.chunk"
    path.write_bytes(_gate(100.0, 100.0) + _gate(100.0, 100.0, 5030.0))
    assert len(fch._crypts_in(str(path), _needle())) == 1


def test_a_gate_off_the_map_is_dropped(tmp_path):
    path = tmp_path / "c.chunk"
    path.write_bytes(_gate(99999.0, 0.0) + _gate(10.0, 10.0))
    assert [round(x) for x, _ in fch._crypts_in(str(path), _needle())] == [10]


def test_a_world_is_read_across_its_chunks(tmp_path):
    (tmp_path / "00.chunk").write_bytes(_gate(1.0, 2.0))
    (tmp_path / "01.chunk").write_bytes(b"meadows" * 100)
    (tmp_path / "02.chunk").write_bytes(_gate(3.0, 4.0))
    got = fch.world_crypts(str(tmp_path))
    assert [(round(x), round(z)) for x, z in got] == [(1, 2), (3, 4)]


def _page(monkeypatch, crypts):
    from skald import app

    monkeypatch.setattr(app, "WORLD_MAPS", {
        "Jotunheim": {"edge": 2048, "seen": 1000, "explored": b"", "pins": []}})
    for name in ("WORLD_PORTALS", "WORLD_BUILT", "WORLD_BOATS",
                 "WORLD_CORPSES"):
        monkeypatch.setattr(app, name, {})
    monkeypatch.setattr(app, "WORLD_CRYPTS", {"Jotunheim": crypts})
    monkeypatch.setattr(app, "terrain_png", lambda w: None)
    return app.render_map("Jotunheim",
                          shared={"Jotunheim": {"edge": 2048, "seen": 1000}},
                          seeds={"Jotunheim": {"seed": 1, "seed_name": "x"}})


def test_the_map_marks_each_crypt_and_counts_them(monkeypatch):
    page = _page(monkeypatch, [(-1830.0, 2410.0), (-2105.0, 2290.0)])
    assert len(re.findall(r'class="pin crypt"', page)) == 2
    box = re.search(r'<label><input[^>]*data-layer="crypt"[^>]*>.*?</label>',
                    page, re.S)
    assert box, "no crypt box in the legend"
    assert " checked" in box.group(0)
    assert "sunken crypts <span>2</span>" in box.group(0)
    assert ".plate.off-crypt .pin.crypt" in page


def test_a_world_without_crypts_offers_no_box_for_them(monkeypatch):
    page = _page(monkeypatch, [])
    assert 'data-layer="crypt"' not in page
    assert 'class="pin crypt"' not in page
