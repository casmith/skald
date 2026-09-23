"""Shared fixtures.

Everything here is invented. No real player names, Steam IDs or world names
live in this repository -- the tracker reads people's play habits, and a
public repo is no place for them.
"""
import gzip
import struct
from datetime import datetime, UTC

import pytest

from skald import app, config

# Invented players and worlds, used everywhere in the tests.
ALFR, BERA, CNUT = "Alfr", "Bera", "Cnut"
STEAM = {ALFR: "76561190000000001", BERA: "76561190000000002",
         CNUT: "76561190000000003"}
WORLD = "Testheim"


def stamp(ts):
    """A Valheim log timestamp, as the server writes it (UTC)."""
    return datetime.fromtimestamp(ts, UTC).strftime("%m/%d/%Y %H:%M:%S")


def line(ts, text):
    return f"{stamp(ts)}: {text}"


def join(ts, player, zdoid="12345:1"):
    """The two lines a join makes: the connection, then the character."""
    return [line(ts, f"Got connection SteamID {STEAM[player]}"),
            line(ts + 20, f"Got character ZDOID from {player} : {zdoid}")]


def leave(ts, player):
    return [line(ts, f"Closing socket {STEAM[player]}")]


def died(ts, player):
    return [line(ts, f"Got character ZDOID from {player} : 0:0")]


def respawned(ts, player, zdoid="777:9"):
    return [line(ts, f"Got character ZDOID from {player} : {zdoid}")]


def world_save(keys, world_time=12345.6):
    """A .db2 file's bytes: header, then the gzip body holding its keys."""
    body = b"\x00\x0cZDO nonsense"
    for k in ["activebosses 0"] + list(keys):
        body += bytes([len(k)]) + k.encode()
    body += b"trailing bytes that start with letters"
    blob = gzip.compress(body)
    return struct.pack("<idi", 41, world_time, len(blob)) + blob


@pytest.fixture
def tracker(tmp_path, request):
    """app, pointed at empty scratch directories and one invented world."""
    events, data, saves, nas = (tmp_path / n for n in
                                ("events", "data", "saves", "nas"))
    for d in (events, data, saves, nas):
        d.mkdir()
    cfg = config.Config(
        events_dir=str(events), data_dir=str(data), saves_root=str(saves),
        backups_root=str(nas), default_world=WORLD, timezone="UTC",
        worlds=(config.World(name=WORLD, status_url="http://127.0.0.1:1/status.json"),))
    before = app.CONFIG
    app.apply_config(cfg)  # closes and forgets any database from a past test
    request.addfinalizer(lambda: app.apply_config(before))
    app.STATUS.clear()
    app.LAST_NONZERO.clear()

    class Tracker:
        module = app
        dirs = {"events": events, "data": data, "saves": saves, "nas": nas}

        def write_events(self, *groups, world=WORLD):
            lines = [ln for group in groups for ln in group]
            (events / f"{world}.log").write_text("\n".join(lines) + "\n")
            app._CACHE.update(sig=None, history=None)
            return app.history()

        def write_save(self, n, keys, mtime, world=WORLD, world_time=12345.6):
            d = saves / world / "worlds_local" / world
            d.mkdir(parents=True, exist_ok=True)
            for old in d.iterdir():
                old.unlink()
            (d / f"_main.{n}.db2").write_bytes(world_save(keys, world_time))
            ok = d / f"_main.{n}.ok"
            ok.write_text("ok")
            import os
            os.utime(ok, (mtime, mtime))

    return Tracker()
