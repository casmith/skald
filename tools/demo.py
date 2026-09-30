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
from skald.worldgen import stable_hash  # noqa: E402

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

    # How the world is set up, logged once at startup exactly as a real
    # server writes it. Midgard runs a couple of things off the defaults;
    # Utgard is vanilla, so it logs nothing at all.
    if world == "Midgard":
        boot = now - days * 86400 - 600
        for key, value in (("combat", "hard"), ("deathpenalty", "casual"),
                           ("resources", "more"), ("raids", "less")):
            lines.append(f"{stamp(boot)}: Setting world modifier: {key}->{value}")

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


# --- the world itself ------------------------------------------------------
#
# Everything the map shows is read out of a world's own save, so the demo
# has to write one: the metadata that carries the seed, and a chunk holding
# a cartography table, some portals, a couple of boats and somebody's
# unlucky afternoon. Invented, like the rest of this file -- the repository
# should never need a real player's name in it, and the map is the part
# most likely to smuggle one in.

MAP_EDGE = 256                   # the smallest a shared map may be
DEMO_PINS = [
    ("Home", -180.0, 240.0, 1), ("mine", 640.0, -220.0, 2),
    ("crypt", -980.0, -640.0, 3), ("silver!", 1180.0, 900.0, 2),
    ("$enemy_eikthyr", 60.0, 700.0, 9), ("camp", 320.0, 420.0, 0),
]
DEMO_PORTALS = [("home", -170.0, 250.0), ("home", 1120.0, 880.0),
                ("swamp", -960.0, -620.0), ("swamp", -150.0, 260.0),
                ("", 700.0, -260.0)]
DEMO_BOATS = [("Karve", -220.0, 300.0), ("VikingShip", -260.0, 330.0)]
# One left on a hillside, one still in a crypt -- the case worth showing,
# since a cave's inside is built five thousand metres above its entrance.
DEMO_CORPSES = [("Bera", 690.0, -250.0, 34.0), ("Cnut", -980.0, -640.0, 5120.0)]


def _string(text):
    """A .NET length-prefixed string, which is what the game writes."""
    raw = text.encode("utf-8")
    n, out = len(raw), bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            break
    return bytes(out) + raw


def world_meta_file(name, seed_name):
    body = (struct.pack("<i", 35) + _string(name) + _string(seed_name)
            + struct.pack("<i", stable_hash(seed_name))
            + struct.pack("<q", abs(stable_hash(name)) or 1))
    return struct.pack("<i", len(body)) + body


def cartography(rng, edge=MAP_EDGE):
    """A believable blot of explored ground, and the pins on it."""
    grid = bytearray(edge * edge)
    blobs = [(0.52, 0.46, 0.085), (0.58, 0.40, 0.05), (0.45, 0.54, 0.045),
             (0.63, 0.55, 0.03), (0.40, 0.38, 0.025)]
    for cx, cy, r in blobs:
        px, py, pr = cx * edge, cy * edge, r * edge
        for y in range(max(0, int(py - pr)), min(edge, int(py + pr) + 1)):
            for x in range(max(0, int(px - pr)), min(edge, int(px + pr) + 1)):
                d = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
                if d < pr * (0.78 + rng.random() * 0.3):
                    grid[y * edge + x] = 1
    pins = bytearray(struct.pack("<i", len(DEMO_PINS)))
    for name, x, z, kind in DEMO_PINS:
        pins += struct.pack("<q", 1)
        pins += _string(name)
        pins += struct.pack("<3f", x, 30.0, z)
        pins += struct.pack("<i", kind)
        pins += bytes([0])
        pins += _string("Steam_76561190000000001")
    return gzip.compress(bytes(grid) + bytes(pins))


def world_objects():
    """Portals, boats, corpses and a village, in the shape the game writes:
    a position, then the prefab's hash, then its fields."""
    out = bytearray()
    for tag, x, z in DEMO_PORTALS:
        out += struct.pack("<3f", x, 31.0, z)
        out += struct.pack("<i", stable_hash("portal_wood"))
        out += struct.pack("<i", stable_hash("creator")) + struct.pack("<q", 1)
        if tag:
            out += struct.pack("<i", stable_hash("tag")) + _string(tag)
    for prefab, x, z in DEMO_BOATS:
        out += struct.pack("<3f", x, 30.0, z) + struct.pack("<i", stable_hash(prefab))
    for who, x, z, y in DEMO_CORPSES:
        out += struct.pack("<3f", x, y, z)
        out += struct.pack("<i", stable_hash("Player_tombstone"))
        out += struct.pack("<i", stable_hash("ownerName")) + _string(who)
    # A cleared patch of forest near the village, plus stragglers further
    # out, so the felled count has a shape and the layer something to show.
    # Deliberately no creator on any of them: the game puts none on a stump,
    # and the tally is the world's rather than anybody's because of it.
    rng = random.Random(7)
    for _ in range(340):
        if rng.random() < 0.75:
            x, z = -150.0 + rng.gauss(0, 60), 230.0 + rng.gauss(0, 60)
        else:
            x, z = rng.uniform(-2000, 2000), rng.uniform(-2000, 2000)
        out += struct.pack("<3f", x, 30.0 + rng.uniform(0, 8), z)
        out += struct.pack("<i", stable_hash(rng.choice(
            ["Beech_Stub", "FirTree_Stub", "BirchStub", "Pinetree_01_Stub"])))
        out += struct.pack("<i", stable_hash("health")) + struct.pack("<f", 1.0)

    # A village around the home portal, so the building layer has something
    # to light up. Only pieces with a creator count as built.
    rng = random.Random(11)
    for _ in range(220):
        x = -180.0 + rng.gauss(0, 34)
        z = 250.0 + rng.gauss(0, 34)
        out += struct.pack("<3f", x, 32.0, z)
        out += struct.pack("<i", stable_hash(rng.choice(
            ["wood_floor", "wood_beam", "wood_roof", "wood_wall_log"])))
        out += struct.pack("<i", stable_hash("creator")) + struct.pack("<q", 1)
    return bytes(out)


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
        # The metadata sits beside the world's directory, not inside it --
        # that is where the game puts it and where Skald looks.
        with open(os.path.join(os.path.dirname(d), f"{world}.fwl"), "wb") as f:
            f.write(world_meta_file(world, world))
        with open(os.path.join(d, "00_00__0_1.chunk"), "wb") as f:
            f.write(world_objects() + cartography(rng))
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
    app.refresh_world_maps()
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
             "diagnostics.html": app.render_diagnostics(app.diagnostics(h, now)),
             # The map, with the fog off: the demo world has been explored
             # about as much as a real one, which is to say hardly, and the
             # point of the picture is the terrain the seed gives.
             "map.html": app.render_map(
                 "Midgard", shared=dict(app.WORLD_MAPS),
                 seeds={w: app.world_metadata(w) for w in WORLDS}, fog=False)}
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
