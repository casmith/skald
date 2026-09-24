#!/usr/bin/env python3
"""Build a world's worth of invented data, and optionally render the pages.

For screenshots, for trying Skald without a Valheim server, and for keeping
real players out of this repository: every name, world and Steam ID here is
made up.

    python3 tools/demo.py --out /tmp/skald-demo
    SKALD_EVENTS_DIR=/tmp/skald-demo/events SKALD_DATA_DIR=/tmp/skald-demo/data \\
    SKALD_SAVES_ROOT=/tmp/skald-demo/saves SKALD_WORLDS=Midgard=http://x/s.json \\
        python3 -m skald

    python3 tools/demo.py --out /tmp/skald-demo --render docs/screenshots
"""
import argparse
import gzip
import os
import random
import struct
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skald import app, config, store  # noqa: E402

WORLDS = ["Midgard", "Utgard"]
# Invented players, with the shape of a real group: one who is always on,
# a couple of regulars, someone who visits.
PLAYERS = [("Alfr", 4), ("Bera", 3), ("Cnut", 2), ("Dagny", 1)]
# One player with a second character, because that is the case worth seeing:
# Skald works out which characters a Steam account plays and calls them by
# the one they play most, and there is nothing to look at unless someone has
# two. Every fifth session, Alfr plays Sigrun instead.
ALTS = {"Alfr": "Sigrun"}
KEYS = {"Midgard": ["defeated_eikthyr", "defeated_gdking", "killedtroll",
                    "defeated_writhan"],
        "Utgard": ["defeated_eikthyr"]}


def stamp(ts):
    return datetime.fromtimestamp(ts, UTC).strftime("%m/%d/%Y %H:%M:%S")


def sessions(rng, world, now, days=30):
    """Plausible evenings: a few players, a few hours, some deaths."""
    lines = []
    for day in range(days, -1, -1):
        for i, (player, keenness) in enumerate(PLAYERS):
            if rng.random() > keenness / 6:
                continue
            start = now - day * 86400 + rng.randint(17, 22) * 3600 + i * 600
            if start > now - 60:
                continue
            steam = f"7656119000000000{i + 1}"
            length = rng.randint(40, 200) * 60
            alt = ALTS.get(player)
            if alt and rng.random() < 0.2:
                player = alt
            lines.append(f"{stamp(start)}: Got connection SteamID {steam}")
            lines.append(f"{stamp(start + 20)}: Got character ZDOID from {player} "
                         f": {rng.randint(1000, 9999)}:1")
            for _ in range(rng.choice([0, 0, 1, 1, 2])):
                at = start + rng.randint(300, max(301, length - 60))
                lines.append(f"{stamp(at)}: Got character ZDOID from {player} : 0:0")
                lines.append(f"{stamp(at + 9)}: Got character ZDOID from {player} "
                             f": {rng.randint(1000, 9999)}:2")
            for _ in range(rng.choice([0, 1, 3, 8])):
                at = start + rng.randint(60, length)
                lines.append(f"{stamp(at)}: Placed location "
                             f"{rng.choice(['Crypt2', 'Ruin1', 'TrollCave02', 'Dolmen01'])} "
                             f"in zone {rng.randint(-40, 40)},{rng.randint(-40, 40)} "
                             "duration 3.2 ms")
            lines.append(f"{stamp(start + length)}: Closing socket {steam}")

    # A boss falling, with the summon before it, so the dashboard shows a
    # kill timed to the second and how long the fight took.
    if world == "Midgard":
        fell = now - 12 * 86400 + 20 * 3600
        steam = "76561190000000001"
        lines += [
            f"{stamp(fell - 3600)}: Got connection SteamID {steam}",
            f"{stamp(fell - 3580)}: Got character ZDOID from Alfr : 4242:1",
            f"{stamp(fell - 480)}: Spawning boss at (1, 2, 3) using spawn point (1,2,3)",
            f"{stamp(fell)}: Setting global key defeated_gdking",
            f"{stamp(fell + 900)}: Closing socket {steam}",
        ]

    # Someone is still on: a session with no Closing socket, which is what
    # "online now" is made of.
    on_now = PLAYERS[0] if world == "Midgard" else PLAYERS[1]
    who = PLAYERS.index(on_now)
    lines += [
        f"{stamp(now - 5400)}: Got connection SteamID 7656119000000000{who + 1}",
        f"{stamp(now - 5380)}: Got character ZDOID from {on_now[0]} : 909{who}:1",
    ]
    if world == "Midgard":  # a second pair of boots on the ground
        lines += [
            f"{stamp(now - 2700)}: Got connection SteamID 76561190000000002",
            f"{stamp(now - 2680)}: Got character ZDOID from Bera : 5150:1",
        ]
    return sorted(lines)


def save_file(keys, world_time):
    body = b"demo" + b"".join(bytes([len(k)]) + k.encode()
                              for k in ["activebosses 0"] + keys)
    blob = gzip.compress(body)
    return struct.pack("<idi", 41, world_time, len(blob)) + blob


def build(out, now):
    rng = random.Random(7)  # same demo every time
    for name in ("events", "data"):
        os.makedirs(os.path.join(out, name), exist_ok=True)
    for world in WORLDS:
        with open(os.path.join(out, "events", f"{world}.log"), "w") as f:
            f.write("\n".join(sessions(rng, world, now)) + "\n")
        d = os.path.join(out, "saves", world, "worlds_local", world)
        os.makedirs(d, exist_ok=True)
        clock = 320_000 if world == "Midgard" else 41_000
        with open(os.path.join(d, "_main.7.db2"), "wb") as f:
            f.write(save_file(KEYS[world], clock))
        with open(os.path.join(d, "_main.7.ok"), "w") as f:
            f.write("ok")
    return config.Config(
        events_dir=os.path.join(out, "events"), data_dir=os.path.join(out, "data"),
        saves_root=os.path.join(out, "saves"), backups_root=os.path.join(out, "nas"),
        timezone="America/Chicago", default_world="Midgard",
        sources={"timezone": "file", "worlds": "file", "port": "default"},
        worlds=tuple(config.World(name=w, status_url=f"http://{w.lower()}/status.json")
                     for w in WORLDS))


def render(cfg, into, now):
    os.makedirs(into, exist_ok=True)
    app.apply_config(cfg)
    app.scan_saves()
    # Pretend the servers answered, so the pages show a world with people on
    # it rather than one that is down.
    for i, world in enumerate(WORLDS):
        app.STATUS[world] = {"up": True, "count": 2 - i, "status_ts": now,
                             "error": None, "game_version": "1.0.15"}
    h = app.history()
    # A signed-in player, so the characters page has something to show. The
    # Steam account is the invented one sessions() gives Alfr, who also plays
    # Sigrun -- which is the whole point of that page.
    steam = "76561190000000001"
    store.put_user(app.db(), steam, {"display_name": "alfr"}, now)
    user = {"steam_id": steam, "display_name": "alfr", "avatar": "",
            "character": None}
    mine = app.sync_user(app.db(), user, h, now)
    pages = {"dashboard.html": app.render(h, now, "Midgard"),
             "characters.html": app.render_me(
                 user, mine, store.characters(app.db(), steam),
                 app.me_stats(h, now, [c["name"] for c in mine], user["character"])),
             "diagnostics.html": app.render_diagnostics(app.diagnostics(h, now))}
    pages["forecast.html"] = pages["dashboard.html"].replace(
        '<details class="forecast">', '<details class="forecast" open>')
    for name, html in pages.items():
        with open(os.path.join(into, name), "w") as f:
            f.write(html)
    return sorted(pages)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="/tmp/skald-demo", help="where to build the data")
    p.add_argument("--render", metavar="DIR", help="also write the pages as HTML here")
    args = p.parse_args()
    now = time.time()
    cfg = build(args.out, now)
    print(f"demo data in {args.out}: {len(WORLDS)} worlds, "
          f"{len(PLAYERS)} players, 30 days")
    if args.render:
        print("rendered:", ", ".join(render(cfg, args.render, now)))
