"""Drawing a world happens away from the request.

It takes minutes of one core. Done inside a request the page simply waits,
and a blank square for six minutes is indistinguishable from a broken one --
which is exactly how it was first reported.
"""
from skald import app


class _Meta(dict):
    pass


def _world(monkeypatch, tmp_path, seed=1234):
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app, "world_metadata",
                        lambda w: {"seed": seed, "seed_name": "x", "uid": 1})
    return "Jotunheim"


def test_serving_never_draws(monkeypatch, tmp_path):
    """The one property that matters: asking for a picture that is not there
    returns nothing, rather than spending six minutes making it."""
    world = _world(monkeypatch, tmp_path)
    drew = []
    monkeypatch.setattr(app.worldgen, "render",
                        lambda *a, **k: drew.append(1) or b"")
    assert app.terrain_png(world) is None
    assert drew == []


def test_what_the_worker_draws_is_what_is_served(monkeypatch, tmp_path):
    world = _world(monkeypatch, tmp_path)
    monkeypatch.setattr(app.worldgen, "render", lambda *a, **k: b"PNGBODY")
    assert app.draw_terrain(world) is True
    assert app.terrain_png(world) == b"PNGBODY"


def test_a_world_already_drawn_is_not_drawn_again(monkeypatch, tmp_path):
    world = _world(monkeypatch, tmp_path)
    monkeypatch.setattr(app.worldgen, "render", lambda *a, **k: b"FIRST")
    app.draw_terrain(world)
    monkeypatch.setattr(app.worldgen, "render", lambda *a, **k: b"SECOND")
    app.draw_terrain(world)
    assert app.terrain_png(world) == b"FIRST"


def test_a_failed_drawing_leaves_nothing_behind(monkeypatch, tmp_path):
    """Half a picture is worse than none: the next reader would serve it."""
    world = _world(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("out of memory")

    monkeypatch.setattr(app.worldgen, "render", boom)
    assert app.draw_terrain(world) is False
    assert app.terrain_png(world) is None
    assert list(tmp_path.iterdir()) == []


def test_progress_is_reported_while_drawing_and_cleared_after(monkeypatch, tmp_path):
    world = _world(monkeypatch, tmp_path)
    seen = []

    def render(_world, _size, progress=None):
        progress(0, 4)
        seen.append(app.TERRAIN_PROGRESS.get("Jotunheim"))
        progress(2, 4)
        seen.append(app.TERRAIN_PROGRESS.get("Jotunheim"))
        return b"X"

    monkeypatch.setattr(app.worldgen, "render", render)
    app.draw_terrain(world)
    assert seen == [0.0, 0.5]
    assert "Jotunheim" not in app.TERRAIN_PROGRESS


def test_the_worker_skips_worlds_that_are_done(monkeypatch, tmp_path):
    world = _world(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "SERVERS", {world: object()})
    monkeypatch.setattr(app.worldgen, "render", lambda *a, **k: b"ONCE")
    app.terrain_worker()
    calls = []
    monkeypatch.setattr(app.worldgen, "render",
                        lambda *a, **k: calls.append(1) or b"AGAIN")
    app.terrain_worker()
    assert calls == []


def test_a_drawing_that_fails_while_writing_leaves_no_part_file(monkeypatch, tmp_path):
    """The rename is what publishes the picture. Failing before it must not
    leave a part file behind: nothing will ever finish or replace it."""
    world = _world(monkeypatch, tmp_path)
    monkeypatch.setattr(app.worldgen, "render", lambda *a, **k: b"BODY")
    real = app.os.replace

    def fail(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(app.os, "replace", fail)
    assert app.draw_terrain(world) is False
    monkeypatch.setattr(app.os, "replace", real)
    assert list(tmp_path.iterdir()) == []
