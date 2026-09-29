"""Reading backups without stalling the machine the game runs on.

A world Skald has not seen before has every backup it has ever taken waiting
to be read, and they live on whatever the backups are kept on -- a NAS, over
the network, on the same box as the game servers. Read back to back and in
full, that is enough to starve everything else of I/O.
"""
import gzip
import struct
import zipfile

from tests.conftest import WORLD, world_save

from skald import app


def _backup(path, world, keys, tail=b""):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr(f"worlds_local/{world}/_main.db2", world_save(keys) + tail)


def test_only_the_head_of_a_save_is_read(tmp_path, monkeypatch):
    """The keys sit in the first few hundred bytes; the rest of a save is
    chunk data of no interest. Reading it whole is tens of megabytes off the
    NAS per backup, for nothing."""
    path = tmp_path / "worlds-20260101-000000.zip"
    _backup(str(path), WORLD, ["defeated_eikthyr"], tail=b"\x00" * (8 << 20))

    read = []
    real_open = zipfile.ZipFile.open

    def counting_open(self, name, *a, **kw):
        f = real_open(self, name, *a, **kw)
        real_read = f.read

        def watched(n=-1):
            data = real_read(n)
            read.append(len(data))
            return data

        f.read = watched
        return f

    monkeypatch.setattr(zipfile.ZipFile, "open", counting_open)
    keys = app.backup_global_keys(str(path), WORLD)

    assert "defeated_eikthyr" in keys
    assert sum(read) < 100_000, f"read {sum(read)} bytes; the tail is 8 MB"


def test_a_save_claiming_an_absurd_body_is_refused(tmp_path):
    """The length comes out of the file, so it is not to be trusted with an
    allocation."""
    path = tmp_path / "worlds-20260101-000000.zip"
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr(f"worlds_local/{WORLD}/_main.db2",
                   struct.pack("<idi", 41, 0.0, 2_000_000_000) + b"rubbish")
    try:
        app.backup_global_keys(str(path), WORLD)
    except ValueError as e:
        assert "implausible" in str(e)
    else:
        raise AssertionError("an absurd length should be refused")


def test_a_new_world_does_not_read_every_backup_at_once(tracker, monkeypatch):
    """The stampede this exists to prevent: a world seen for the first time
    has no watermark, so every backup it owns is new."""
    nas = tracker.dirs["nas"] / WORLD.lower() / "backups"
    nas.mkdir(parents=True)
    for i in range(20):
        _backup(str(nas / f"worlds-20260101-{i:02d}0000.zip"),
                WORLD, ["defeated_eikthyr"])

    opened = []
    real = app.backup_global_keys
    monkeypatch.setattr(app, "backup_global_keys",
                        lambda p, w: opened.append(p) or real(p, w))

    app.scan_backups()
    assert len(opened) == app.BACKUPS_PER_SCAN, (
        f"read {len(opened)} backups in one pass, not {app.BACKUPS_PER_SCAN}")


def test_it_catches_up_over_the_next_passes(tracker, monkeypatch):
    """Bounded, not abandoned: the rest arrive on later scans."""
    nas = tracker.dirs["nas"] / WORLD.lower() / "backups"
    nas.mkdir(parents=True)
    for i in range(14):
        _backup(str(nas / f"worlds-20260101-{i:02d}0000.zip"),
                WORLD, ["defeated_eikthyr"])

    seen = set()
    real = app.backup_global_keys
    monkeypatch.setattr(app, "backup_global_keys",
                        lambda p, w: seen.add(p) or real(p, w))

    for _ in range(4):
        app.scan_backups()
    assert len(seen) == 14, f"only {len(seen)} of 14 backups were ever read"
