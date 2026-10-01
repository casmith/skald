"""Stumps: counted, and counted over time.

Nothing in a save records who felled a tree -- a stump is a destructible,
not a built piece, so it carries no creator and no name. Verified against
1,375 real stumps across three worlds: a creator sat in the slot a build
piece keeps one in exactly zero times. So this is a fact about a world, and
these tests hold it to that.
"""
import struct

from skald import fch, store
from skald.worldgen import stable_hash


def _chunk(tmp_path, name, objects):
    """A chunk holding each (prefab, x, y, z) at the real ZDO offset."""
    blob = bytearray(b"\x00" * 64)
    for prefab, x, y, z in objects:
        blob += struct.pack("<3f", x, y, z)
        blob += struct.pack("<i", stable_hash(prefab))
        blob += b"\x00" * 40
    p = tmp_path / name
    p.write_bytes(bytes(blob))
    return p


def test_every_stump_prefab_is_counted(tmp_path):
    _chunk(tmp_path, "a.chunk",
           [(n, 100.0 * i, 30.0, 0.0) for i, n in enumerate(fch.STUMPS)])
    assert len(fch.world_stumps(str(tmp_path))) == len(fch.STUMPS)


def test_a_stump_keeps_its_place(tmp_path):
    _chunk(tmp_path, "a.chunk", [("Beech_Stub", -1234.5, 42.0, 678.25)])
    (x, z), = fch.world_stumps(str(tmp_path))
    assert round(x, 2) == -1234.5 and round(z, 2) == 678.25


def test_a_stump_underground_or_in_the_sky_is_a_bad_match(tmp_path):
    """Five prefabs are looked for by a four-byte hash, so some hits are
    chance. A stump sits on the ground it grew on."""
    _chunk(tmp_path, "a.chunk", [("Beech_Stub", 0.0, 5000.0, 0.0),
                                 ("FirTree_Stub", 0.0, -900.0, 0.0),
                                 ("BirchStub", 0.0, 30.0, 0.0)])
    assert len(fch.world_stumps(str(tmp_path))) == 1


def test_a_living_tree_is_not_a_stump(tmp_path):
    _chunk(tmp_path, "a.chunk", [("Beech1", 0.0, 30.0, 0.0),
                                 ("FirTree", 10.0, 30.0, 0.0),
                                 ("Pinetree_01", 20.0, 30.0, 0.0)])
    assert fch.world_stumps(str(tmp_path)) == []


def test_a_world_nobody_has_logged_reads_as_nothing(tmp_path):
    _chunk(tmp_path, "a.chunk", [])
    assert fch.world_stumps(str(tmp_path)) == []


# ---- the series ----------------------------------------------------------

def _db(tmp_path):
    return store.connect(str(tmp_path))


def test_a_count_that_moved_is_kept(tmp_path):
    db = _db(tmp_path)
    assert store.put_stumps(db, "Midgard", 10, 1000.0) is True
    assert store.put_stumps(db, "Midgard", 11, 1060.0) is True
    assert store.stump_history(db, "Midgard") == [(1000.0, 10), (1060.0, 11)]


def test_a_count_standing_still_is_not_written_every_scan(tmp_path):
    """A scan every half hour would otherwise write 48 identical rows a day."""
    db = _db(tmp_path)
    store.put_stumps(db, "Midgard", 10, 1000.0)
    for t in range(1060, 1000 + 86400, 1800):
        assert store.put_stumps(db, "Midgard", 10, float(t)) is False
    assert len(store.stump_history(db, "Midgard")) == 1


def test_a_day_of_standing_still_is_still_worth_a_row(tmp_path):
    """Otherwise a world nobody plays leaves a gap that reads as missing
    data rather than as a flat line."""
    db = _db(tmp_path)
    store.put_stumps(db, "Midgard", 10, 1000.0)
    assert store.put_stumps(db, "Midgard", 10, 1000.0 + 86401) is True
    assert len(store.stump_history(db, "Midgard")) == 2


def test_each_world_counts_separately(tmp_path):
    db = _db(tmp_path)
    store.put_stumps(db, "Midgard", 10, 1000.0)
    store.put_stumps(db, "Utgard", 99, 1000.0)
    assert store.stump_history(db, "Midgard") == [(1000.0, 10)]
    assert store.stump_history(db, "Utgard") == [(1000.0, 99)]


# ---- what the pages do with it -------------------------------------------

def test_the_card_is_styled_by_the_page_it_appears_in():
    """The first cut put these rules in ME_PAGE while the card renders in
    PAGE, so the sparkline would have shipped unstyled. Templates are
    separate strings and nothing else would have said so."""
    from skald import app
    assert "__FELLED__" in app.PAGE
    assert ".felled .spark" in app.PAGE


def test_never_sampled_shows_nothing_rather_than_a_zero():
    from skald import app
    assert app.render_felled([], 1000.0) == ""


def test_one_sample_gives_the_count_but_claims_no_trend():
    from skald import app
    out = app.render_felled([(1000.0, 42)], 1000.0)
    assert "42" in out and "felled" in out
    assert "+" not in out and "<svg" not in out


def test_a_week_of_chopping_reads_as_a_gain():
    from skald import app
    now = 10 * 86400.0
    hist = [(now - 7 * 86400, 100), (now - 3 * 86400, 140), (now, 175)]
    out = app.render_felled(hist, now)
    assert "175" in out
    assert "+75 this week" in out
    assert "<svg" in out and "polyline" in out


def test_a_shorter_history_says_so_far_not_this_week():
    from skald import app
    now = 10 * 86400.0
    hist = [(now - 3600, 10), (now, 20)]
    out = app.render_felled(hist, now)
    assert "+10 so far" in out


def test_a_flat_week_draws_no_line_and_claims_no_change():
    """min == max, so the sparkline would divide by zero."""
    from skald import app
    now = 10 * 86400.0
    hist = [(now - 2 * 86400, 50), (now - 86400, 50), (now, 50)]
    out = app.render_felled(hist, now)
    assert "50" in out
    assert "<svg" not in out
    assert "+" not in out


def test_a_number_that_went_down_is_reported_as_such():
    """Stumps can be cleared, so the count is not monotonic."""
    from skald import app
    now = 10 * 86400.0
    out = app.render_felled([(now - 86400, 90), (now, 80)], now)
    assert "-10" in out


def test_a_reader_that_disowns_a_file_does_not_break_the_scan(tmp_path):
    """_portals_in returns None rather than a half-read file, and unifying
    six copies of the scan loop dropped the guard that allowed for it. The
    demo world caught this; no test did."""
    (tmp_path / "a.chunk").write_bytes(b"\x00" * 32)
    assert fch._scan_chunks(str(tmp_path), None, lambda p: None) == []
    assert fch._scan_chunks(str(tmp_path), {}, lambda p: None) == []
    assert fch._scan_chunks(str(tmp_path), None, lambda p: [1, 2]) == [1, 2]


# ---- how big a stump is drawn --------------------------------------------

def _lit(points, stamp, edge=256):
    """How many pixels an overlay actually turns on."""
    import zlib
    png = fch.ore_png(points, edge, (1, 2, 3), stamp=stamp)
    # the IDAT is the only compressed chunk; walk the PNG to find it
    i, idat = 8, b""
    while i < len(png):
        n = int.from_bytes(png[i:i + 4], "big")
        kind = png[i + 4:i + 8]
        if kind == b"IDAT":
            idat += png[i + 8:i + 8 + n]
        i += 12 + n
    raw = zlib.decompress(idat)
    stride = edge + 1
    return sum(1 for y in range(edge)
               for x in range(edge) if raw[y * stride + 1 + x])


def test_a_stump_is_one_pixel_and_a_vein_is_a_cross():
    """The cross is 36 metres across at the real scale. That is right for a
    vein you are hunting and wrong for a stump: twenty in a clearing became
    one blob that read as a field full of them."""
    one = [(0.0, 0.0)]
    assert _lit(one, fch.DOT) == 1
    assert _lit(one, fch.CROSS) == 5


def test_the_ore_layers_were_left_alone():
    """Only the stumps wanted the smaller mark."""
    import inspect
    assert inspect.signature(
        fch.ore_png).parameters["stamp"].default is fch.CROSS


def test_a_felled_clearing_no_longer_smears_past_itself():
    """At the resolution the map is drawn at -- 2048 px over 24,576 m, so 12 m
    a pixel -- a dot is one mark per stump and a cross is five. Twenty stumps
    in a clearing is the case that mattered: the crosses ran together into a
    blob reaching well past the trees that were felled."""
    clearing = [(x * 13.0, 0.0) for x in range(20)]
    dots = _lit(clearing, fch.DOT, edge=2048)
    crosses = _lit(clearing, fch.CROSS, edge=2048)
    assert dots == 20                      # one mark each, none merged
    assert crosses > 3 * dots              # and the old stamp smeared

    # Stumps in adjacent pixels: dots stay separate, crosses become one bar.
    tight = [(0.0, 0.0), (12.0, 0.0), (24.0, 0.0)]
    assert _lit(tight, fch.DOT, edge=2048) == 3
    assert _lit(tight, fch.CROSS, edge=2048) < 15
