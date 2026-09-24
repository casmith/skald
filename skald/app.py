#!/usr/bin/env python3
"""
skald: who is on the Valheim servers right now, how long each player has
played and how often they have died, how far the world has been explored,
when the bosses fell, and what the weather is doing.

Not the in-game day: the "World time" in Valheim's autosave line looked like
the world clock but is seconds since the server process started -- it
restarts from zero with the server. The real clock is only in the world save.

Valheim's Steam query (the servers' status.json) reports a player *count* but
blanks every name, so names have to come from the server log. Each game
container runs a log-filter hook (see compose.example.yaml)
that appends the few lines that matter to /events/<World>.log:

  Got connection SteamID <id>                  a client connected
  Got character ZDOID from <name> : <a>:<b>    ...and picked this character;
                                               0:0 means that character died
  Closing socket <id>                          a client left
  Game - OnApplicationQuit                     the server shut down
  Placed location <Name> in zone <x>,<y>       a landmark generated as a
                                               player first reached its zone

History is rebuilt from those files whenever one changes -- they are small
and append-only, so there is no database to migrate or get out of step. The one
thing the log cannot say is "the server crashed with people on it", so a
poller watches each status.json and, when it reports nobody online while a
session is still open, appends a close marker of its own to
/data/<World>.reconcile.log.

  /               HTML page (online now, playtime, deaths, last 30 days)
  /api/online     who is on each server
  /api/playtime   per-player hours and deaths for 24h / 7d / 30d / all time
  /api/sessions   recent sessions (?limit=N, default 100)
  /api/deaths     recent deaths (?limit=N, default 100)
  /api/daily      hours played, deaths and new landmarks per day (?days=N)
  /api/milestones bosses and other firsts, with the window each happened in
  /api/weather    each world's in-game clock and per-biome weather
  /healthz        liveness probe

Python stdlib only.
"""
import glob
import gzip
import html
import json
import os
import re
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile

from skald import __version__
from skald import auth
from skald import config as configuration
from skald import store
from skald import weather
from datetime import datetime, timedelta, UTC
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Settings come from a TOML file and the environment -- see config.py. They
# are unpacked into module-level names here because everything below reads
# them as constants, and because tests can then point one at a temp dir.
CONFIG = configuration.load()

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(CONFIG.timezone)
except Exception:  # unknown zone, or no tzdata: days are UTC days instead
    LOCAL_TZ = UTC

PORT = CONFIG.port
EVENTS_DIR = CONFIG.events_dir
DATA_DIR = CONFIG.data_dir
POLL_SECONDS = CONFIG.poll_seconds
# A drop-and-rejoin inside this many seconds is one session, not two.
MERGE_GAP = CONFIG.merge_gap_seconds
# A connection that never picks a character within this long is abandoned
# (wrong password, version mismatch) and must not claim a later player's name.
PENDING_TTL = 300
CHART_DAYS = CONFIG.chart_days
# World -> status URL. A world's name must match the game's WORLD_NAME, which
# is what the log hook names its events file after.
SERVERS = {w.name: w.status_url for w in CONFIG.worlds}
# The tab the page opens on; the first world when unset.
DEFAULT_WORLD = CONFIG.default_world

WINDOWS = [("24h", 86400), ("7d", 7 * 86400), ("30d", 30 * 86400)]

# Milestones (boss kills and other firsts) are global keys the world sets and
# saves. Three sources date them, best first:
#   1. the server log -- "Setting global key <key>" from the hook: exact;
#   2. the live autosaves, <SAVES_ROOT>/<World>/worlds_local/<World>/, read
#      every minute: a key new in a save is pinned between that save and the
#      one before it -- 30 minutes;
#   3. the hourly backups on the NAS, <BACKUPS_ROOT>/<world, lowercased>/
#      backups/worlds-YYYYMMDD-HHMMSS.zip -- ~90 minutes, but they reach back
#      14 days, so they date what happened before 1 and 2 were watching.
# What 2 and 3 find is kept in the database: saves roll over and backups are
# pruned, and a milestone must outlive the files that dated it.
BACKUPS_ROOT = CONFIG.backups_root
SAVES_ROOT = CONFIG.saves_root
SAVE_SCAN_SECONDS = CONFIG.save_scan_seconds
BACKUP_SCAN_EVERY = 10  # save scans, i.e. every 10 minutes
# A boss summoned this long before its kill counts as that fight's start.
FIGHT_MAX_SECONDS = 3600
# Log timestamps are to the second and file times can land a moment either
# side of them, so a log time this close to a window's edge still counts.
EDGE_TOLERANCE = 120
# A backup holds the world as of its last autosave, up to 30 minutes older
# than the backup, so a flag can have been set that long before the last
# backup that lacks it.
AUTOSAVE_SLACK = 1800
BACKUP_NAME_RE = re.compile(r"worlds-(\d{8})-(\d{6})\.zip$")
# A world's global keys are length-prefixed strings, saved lowercased; the
# milestone ones all start like this. The key is read as exactly as many
# bytes as its length byte says -- a greedy match could run on into whatever
# follows -- and must then be a whole key, which rules out stray substrings.
GLOBAL_KEY_RE = re.compile(rb"[\x01-\x7f](?=defeated|killed|bosshildir)")
MILESTONE_KEY_RE = re.compile(r"^(?:defeated|killed|bosshildir)[a-z0-9_]*$")
# Valheim moves, and Skald reads files it does not own. Rather than pretend
# otherwise, it records what it met and says so on /diagnostics: the save
# format's version number, the game version the servers report, and lines
# that look like the game talking but match nothing Skald knows.
KNOWN_SAVE_VERSIONS = (41,)
# The game versions the weather tables and log patterns were checked against.
VERIFIED_GAME_VERSIONS = ("1.0",)
# The status endpoint's keywords carry the game version: "g=1.0.15,n=40,m="
GAME_VERSION_RE = re.compile(r"\bg=([0-9][0-9.]*)")
MILESTONES = {
    # key: (label, kind). Unknown keys still show, with a generated label.
    "defeated_eikthyr": ("Eikthyr defeated", "boss"),
    "defeated_gdking": ("The Elder defeated", "boss"),
    "defeated_bonemass": ("Bonemass defeated", "boss"),
    "defeated_dragon": ("Moder defeated", "boss"),
    "defeated_goblinking": ("Yagluth defeated", "boss"),
    "defeated_queen": ("The Queen defeated", "boss"),
    "defeated_fader": ("Fader defeated", "boss"),
    # The mini-bosses of Hildir's quests, in quest order. Lord Reto's key is
    # not known here; it will show with a generated label.
    "bosshildir1": ("Brenna defeated (Hildir's first chest)", "mini-boss"),
    "bosshildir2": ("Geirrhafa defeated (Hildir's second chest)", "mini-boss"),
    "bosshildir3": ("Zil & Thungr defeated (Hildir's third chest)", "mini-boss"),
    # Rare Swamp creature, not a mini-boss. The only known effect of its key
    # is that the Bog Witch starts selling the Crown of Roots.
    "defeated_writhan": ("First Writhan killed", "rare"),
    "defeated_serpent": ("First sea serpent killed", "first"),
    "killedtroll": ("First troll killed", "first"),
    "killedbat": ("First bat killed", "first"),
    "killed_surtling": ("First surtling killed", "first"),
}
# The main bosses in progression order: shown as achievement badges, locked
# and nameless until beaten. Everything else in MILESTONES is listed plainly.
BOSSES = [
    ("defeated_eikthyr", "Eikthyr"), ("defeated_gdking", "The Elder"),
    ("defeated_bonemass", "Bonemass"), ("defeated_dragon", "Moder"),
    ("defeated_goblinking", "Yagluth"), ("defeated_queen", "The Queen"),
    ("defeated_fader", "Fader"),
]
NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII"]

# Valheim stamps its own lines, in the container's timezone -- Etc/UTC unless
# TZ is set on the game container, which none of ours do.
TS_RE = re.compile(r"(\d\d)/(\d\d)/(\d{4}) (\d\d):(\d\d):(\d\d): ")
EVENT_RES = [
    # (kind, regex, sort priority within the same second). A close sorts
    # before a connect so a same-second reconnect ends the old session first.
    ("close", re.compile(r"Closing socket (\d+)"), 0),
    ("connect", re.compile(r"Got connection SteamID (\d+)"), 1),
    ("character", re.compile(r"Got character ZDOID from (.+) : (-?\d+):(-?\d+)"), 2),
    ("quit", re.compile(r"OnApplicationQuit"), 3),
    ("gone", re.compile(r"\[tracker\] no players online"), 3),
    ("location", re.compile(r"Placed location (\S+) in zone (-?\d+),(-?\d+)"), 3),
    ("globalkey", re.compile(r"Setting global key (\S+)"), 3),
    ("bossspawn", re.compile(r"Spawning boss at"), 3),
    # World modifiers, logged once at startup and nowhere else. A preset is
    # logged as itself and does *not* expand into the individual settings,
    # so both shapes have to be kept.
    ("modifier", re.compile(r"Setting world modifier: (\w+)->(\w+)"), 3),
    ("preset", re.compile(r"Setting world modifier preset: (\w+)"), 3),
]

# What the game calls each setting, and each of its values, in words. A name
# missing from here still shows, tidied up: Valheim can add a modifier
# without telling us, and a blank is worse than an unstyled label.
# `m=` is last in the Steam tags and its own commas are backslash-escaped,
# so everything after it is its value.
MODIFIER_TAG_RE = re.compile(r"(?:^|,)m=(.*)$")

# Also the order they are shown in: the game logs them in whatever order it
# happens to hold them, which is neither stable nor meaningful.
MODIFIER_LABELS = {
    "combat": "Combat", "deathpenalty": "Death penalty", "resources": "Resources",
    "raids": "Raids", "portals": "Portals",
}
MODIFIER_ORDER = list(MODIFIER_LABELS)
MODIFIER_VALUES = {
    "veryeasy": "very easy", "easy": "easy", "normal": "normal", "hard": "hard",
    "veryhard": "very hard", "casual": "casual", "hardcore": "hardcore",
    "muchless": "much less", "less": "less", "more": "more",
    "muchmore": "much more", "none": "none", "immersive": "immersive",
}

LOCK = threading.Lock()
# World -> latest poll result: {"up", "count", "status_ts", "error"}.
STATUS = {}
# World -> status_ts of the last poll that saw anyone online.
LAST_NONZERO = {}


def parse_line(line):
    m = TS_RE.search(line)
    if not m:
        return None
    mo, d, y, hh, mi, ss = map(int, m.groups())
    ts = datetime(y, mo, d, hh, mi, ss, tzinfo=UTC).timestamp()
    body = line[m.start():].strip()
    for kind, rx, prio in EVENT_RES:
        em = rx.search(body)
        if em:
            return ts, prio, kind, em.groups(), body
    return None


def apply_config(cfg):
    """Point the module at a different config: used by main() and by tests."""
    global CONFIG, LOCAL_TZ, PORT, EVENTS_DIR, DATA_DIR, POLL_SECONDS, MERGE_GAP
    global CHART_DAYS, SERVERS, DEFAULT_WORLD, BACKUPS_ROOT, SAVES_ROOT
    global SAVE_SCAN_SECONDS
    CONFIG = cfg
    try:
        from zoneinfo import ZoneInfo
        LOCAL_TZ = ZoneInfo(cfg.timezone)
    except Exception:
        LOCAL_TZ = UTC
    PORT, EVENTS_DIR, DATA_DIR = cfg.port, cfg.events_dir, cfg.data_dir
    POLL_SECONDS, MERGE_GAP = cfg.poll_seconds, cfg.merge_gap_seconds
    CHART_DAYS, SAVE_SCAN_SECONDS = cfg.chart_days, cfg.save_scan_seconds
    BACKUPS_ROOT, SAVES_ROOT = cfg.backups_root, cfg.saves_root
    SERVERS = {w.name: w.status_url for w in cfg.worlds}
    DEFAULT_WORLD = cfg.default_world
    # A new config means a new data directory, so the old database is not
    # ours any more.
    global _DB
    with _DB_LOCK:
        if _DB is not None:
            _DB.close()
        _DB = None
    _CACHE.update(sig=None, history=None)
    return cfg


def event_paths():
    return sorted(glob.glob(os.path.join(EVENTS_DIR, "*.log")))


def db():
    """The database, opened once and shared."""
    global _DB
    with _DB_LOCK:
        if _DB is None:
            _DB = store.connect(DATA_DIR)
            imported = store.import_legacy(_DB, DATA_DIR)
            if imported:
                print(f"imported {imported} milestones from milestones.json", flush=True)
        return _DB


def looks_like_log(line):
    """A line with the game's own timestamp on it, whatever it then says."""
    return TS_RE.search(line) is not None


def unwrap(line):
    """A log line, however it is wrapped.

    A server's own log gives the line as written. A container's json log
    file wraps each one in an object with the text under "log" -- which is
    how Skald can read container logs without being handed the Docker
    socket.
    """
    if line.startswith("{") and '"log"' in line:
        try:
            return json.loads(line).get("log", line).rstrip("\n")
        except ValueError:
            return line
    return line


def parse_any(line):
    return parse_line(unwrap(line))


def ingest_events():
    """Take in whatever has been written since last time.

    Two kinds of source, read the same way: the files the log hook writes,
    one per world, and a server's own log file for worlds configured that
    way. Returns how many lines were new.
    """
    added = 0
    for path in event_paths():
        world = os.path.basename(path).split(".", 1)[0]
        # A hook file holds only the lines Skald asked for, so anything in
        # it that matches no pattern is worth counting: that is what a
        # Valheim update rewording a line looks like from here.
        added += store.ingest(db(), path, world, parse_any, looks_like_log)
    for world, path in CONFIG.log_files().items():
        # A server's whole log is mostly lines Skald does not care about,
        # so counting the misses there would say nothing.
        added += store.ingest(db(), path, world, parse_any)
    if added:
        # Whoever ingested, the replay is now out of date. Invalidating here
        # rather than in history() means it cannot matter who called first.
        with _CACHE_LOCK:
            _CACHE["history"] = None
    return added


def load_events():
    """World -> its events, in the order the replay wants them."""
    return store.events(db())


def replay_world(world, evs, out):
    """Replay one world's events into `out`, which holds the shared lists.

    Split out of build_history so the helpers below close over this world's
    state alone: when they lived in the loop they captured whichever world
    came last, which happened to be harmless and would not have stayed that
    way.
    """
    pending = {}  # steamid -> connect time, not yet bound to a character
    online = {}   # player -> open session
    last = {}     # player -> most recent closed session
    zones = set()

    def close(name, ts, seen=True):
        """End a session. `seen` is False when the log never said so -- we
        inferred it -- and such a session must not be merged with a later
        one, or a crash and a rejoin hours later become a single session
        with the time between counted as play."""
        s = online.pop(name)
        s["end"] = max(ts, s["start"])
        s["seen_leave"] = seen
        last[name] = s

    for ts, _, kind, args in evs:
        if kind == "connect":
            sid = args[0]
            for name, s in list(online.items()):
                if s["steamid"] == sid:  # we missed its Closing socket
                    close(name, ts, seen=False)
            pending[sid] = ts
        elif kind == "character":
            name, a, b = args
            if (a, b) == ("0", "0"):
                out["deaths"].append({"world": world, "player": name, "ts": ts})
                continue
            if name in online:  # respawn after a death: same session
                continue
            for sid in [k for k, v in pending.items() if ts - v > PENDING_TTL]:
                del pending[sid]
            sid = min(pending, key=pending.get) if pending else None
            start = pending.pop(sid) if sid else ts
            prev = last.get(name)
            if prev and prev.get("seen_leave", True) and start - prev["end"] <= MERGE_GAP:
                prev["end"], prev["steamid"] = None, sid
                online[name] = prev
            else:
                s = {"world": world, "player": name, "steamid": sid,
                     "start": start, "end": None}
                out["sessions"].append(s)
                online[name] = s
        elif kind == "close":
            pending.pop(args[0], None)
            for name, s in list(online.items()):
                if s["steamid"] == args[0]:
                    close(name, ts)
        elif kind == "globalkey":
            key = args[0].lower()
            if MILESTONE_KEY_RE.match(key):
                out["keys"].append({"world": world, "key": key, "ts": ts})
        elif kind == "bossspawn":
            out["spawns"].append({"world": world, "ts": ts})
        elif kind in ("modifier", "preset"):
            out["modifiers"].append({"world": world, "ts": ts, "kind": kind,
                                     "key": args[0],
                                     "value": args[1] if kind == "modifier" else ""})
        elif kind == "location":
            # Several locations can land in one zone; count the zone once.
            zone = f"{args[1]},{args[2]}"
            if zone not in zones:
                zones.add(zone)
                out["explored"].append({"world": world, "ts": ts, "zone": zone})
        else:  # quit / gone: everyone on this world is off
            for name in list(online):
                close(name, ts, seen=kind == "quit")
            pending.clear()


def build_history():
    """Replay each world's events. Returns a dict of:

      sessions  [{world, player, steamid, start, end}], end None while online
      deaths    [{world, player, ts}]
      explored  [{world, ts, zone}], one per zone first generated with a
                landmark in it -- zones without one are never logged
      keys      [{world, key, ts}], milestone keys the server logged setting
      spawns    [{world, ts}], boss summons
    """
    out = {"sessions": [], "deaths": [], "explored": [], "keys": [], "spawns": [],
           "modifiers": []}
    for world, evs in load_events().items():
        replay_world(world, evs, out)
    sessions, deaths, explored = out["sessions"], out["deaths"], out["explored"]
    keys, spawns = out["keys"], out["spawns"]
    out["modifiers"].sort(key=lambda m: m["ts"])
    for s in sessions:
        s.pop("seen_leave", None)  # internal bookkeeping, not part of the API
    sessions.sort(key=lambda s: s["start"])
    deaths.sort(key=lambda d: d["ts"])
    explored.sort(key=lambda e: e["ts"])
    keys.sort(key=lambda k: k["ts"])
    spawns.sort(key=lambda k: k["ts"])
    return {"sessions": sessions, "deaths": deaths, "explored": explored,
            "keys": keys, "spawns": spawns, "modifiers": out["modifiers"]}


_CACHE = {"sig": None, "history": None}
_CACHE_LOCK = threading.RLock()
_DB, _DB_LOCK = None, threading.RLock()


def history():
    """The replay, redone only when new events have arrived.

    The page can be public, and a full replay grows with the history -- this
    keeps a request's cost flat however often it is made. Callers must not
    mutate the result; it is shared between requests.
    """
    with _CACHE_LOCK:
        ingest_events()
        if _CACHE["history"] is None:
            _CACHE["history"] = build_history()
        return _CACHE["history"]


def poll_once():
    open_by_world = {}
    for s in history()["sessions"]:
        if s["end"] is None:
            open_by_world.setdefault(s["world"], []).append(s)
    for world, url in SERVERS.items():
        st = {"up": False, "count": None, "status_ts": None, "error": None,
              "modified": None}  # None: we have not heard from this world
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.load(r)
            st["status_ts"] = datetime.fromisoformat(
                data["last_status_update"]).timestamp()
            st["error"] = data.get("error")
            keywords = data.get("keywords") or ""
            found = GAME_VERSION_RE.search(keywords)
            st["game_version"] = found.group(1) if found else None
            # The server advertises its world modifiers as `m=`, last in the
            # tags: empty at the defaults, and otherwise a list of numeric
            # effect ids. Those ids are undocumented, built at runtime and
            # free to be renumbered by any update, so Skald reads this as a
            # yes/no and takes the words themselves from the log. Between
            # them: this says *whether* a world is modified even when its
            # startup went unwatched, and the log says *which*.
            mods = MODIFIER_TAG_RE.search(keywords)
            st["modified"] = bool(mods and mods.group(1).strip())
            # The updater keeps rewriting status.json while the game is down,
            # with `error` set -- so a fresh file is not by itself "up".
            fresh = time.time() - st["status_ts"] < 300
            st["up"] = fresh and not st["error"]
            if st["up"]:
                st["count"] = int(data.get("player_count") or 0)
        except Exception as e:  # container stopped, or mid-restart
            st["error"] = str(e)
        with LOCK:
            STATUS[world] = st
        if not st["up"]:
            continue
        if st["count"] > 0:
            LAST_NONZERO[world] = st["status_ts"]
            continue
        # Nobody online per the query, but a session is open: its Closing
        # socket was never logged (crash, kill -9). Close it at the last time
        # we saw anyone -- but only once the status post-dates the newest
        # session by a margin, because status.json lags a fresh join.
        opened = open_by_world.get(world)
        if not opened or st["status_ts"] - max(s["start"] for s in opened) < 120:
            continue
        # Never before the newest open session, or the marker would sort
        # ahead of it, close nothing, and be re-appended every poll.
        at = max(LAST_NONZERO.get(world, st["status_ts"]),
                 max(s["start"] for s in opened))
        stamp = datetime.fromtimestamp(at, UTC).strftime("%m/%d/%Y %H:%M:%S")
        store.add_event(db(), world, f"{stamp}: [skald] no players online",
                        at, 3, "gone", ())
        with _CACHE_LOCK:
            _CACHE["history"] = None


def poller():
    while True:
        try:
            poll_once()
        except Exception as e:
            print(f"poll failed: {e}", flush=True)
        time.sleep(POLL_SECONDS)


def db2_world(b):
    """(keys, world clock, save format version) from a world save (.db2)."""
    # int version, double world time, then the gzip body behind its length.
    version, world_time, ln = struct.unpack_from("<idi", b, 0)
    raw = gzip.decompress(b[16:16 + ln])
    keys = set()
    for m in GLOBAL_KEY_RE.finditer(raw):
        key = raw[m.start() + 1:m.start() + 1 + raw[m.start()]].decode("latin-1")
        if MILESTONE_KEY_RE.match(key):
            keys.add(key)
    return keys, world_time, version


def backup_global_keys(path, world):
    """The milestone keys in one backup's saved world."""
    with zipfile.ZipFile(path) as z:
        dbs = [n for n in z.namelist()
               if f"worlds_local/{world}/" in n and n.endswith(".db2")]
        if not dbs:
            return set()
        return db2_world(z.read(dbs[0]))[0]


def load_milestones():
    """Milestone and save state, in the shape the rest of the code expects."""
    return store.state(db())


def scan_backups():
    """Record each world's milestones, from backups not yet scanned.

    For each key: `after` is the newest backup that lacked it (None if the
    oldest backup scanned already had it), `by` the first that had it.
    """
    state = load_milestones()
    for world in SERVERS:
        st = state.get(world) or {"last": "", "last_ts": None, "milestones": {}}
        known = set(st["milestones"])
        pattern = os.path.join(CONFIG.backups_dir(world), "worlds-*.zip")
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)
            m = BACKUP_NAME_RE.search(name)
            if not m or name <= (st["last"] or ""):
                continue
            try:
                keys = backup_global_keys(path, world)
            except Exception as e:
                # Likely still being written. Stop here and retry next scan
                # rather than skip it and misdate whatever it holds.
                print(f"milestones: {name} unreadable, retrying later: {e}", flush=True)
                break
            ts = datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").replace(
                tzinfo=UTC).timestamp()
            for k in keys - known:
                store.put_milestone(db(), world, k, st["last_ts"], ts, "backup")
                known.add(k)
            store.put_backup(db(), world, name, ts)
            st["last"], st["last_ts"] = name, ts


def latest_save(world):
    """(name, completed_at, path to .db2) of a world's newest finished save.

    A save is _main.<n>.{db2,fwl2,chunks,ok}; the .ok is written once the
    rest is on disk, so its mtime is when that save completed.
    """
    d = CONFIG.saves_dir(world)
    best = None
    for ok in glob.glob(os.path.join(d, "_main.*.ok")):
        m = re.search(r"_main\.(\d+)\.ok$", ok)
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), ok)
    if not best:
        return None
    base = best[1][:-len(".ok")]
    return os.path.basename(base), os.stat(best[1]).st_mtime, base + ".db2"


def scan_saves():
    """Compare each world's newest autosave with the last one seen.

    A key in this save but not the last was set between the two, and both
    completion times are known: a 30-minute window, no slack needed. The
    first save ever seen only sets the baseline.
    """
    state = load_milestones()
    for world in SERVERS:
        try:
            found = latest_save(world)
            if not found:
                continue
            name, done, db2_path = found
            sv = (state.get(world) or {}).get("save") or {
                "last": "", "last_ts": None, "keys": None}
            if name == sv["last"] and done == sv["last_ts"]:
                continue
            with open(db2_path, "rb") as f:
                keys, world_time, save_version = db2_world(f.read())
            if save_version not in KNOWN_SAVE_VERSIONS:
                # Newer than anything Skald has been shown. The header has
                # not moved in a long time, so read it anyway -- but say so.
                print(f"note: {world}'s save is format {save_version}, which this "
                      f"version of skald has not seen (known: "
                      f"{', '.join(map(str, KNOWN_SAVE_VERSIONS))}). See /diagnostics.",
                      flush=True)
        except Exception as e:
            # Mid-save or rolled over under us: try again next minute.
            print(f"milestones: {world} save unreadable, retrying: {e}", flush=True)
            continue
        live = (state.get(world) or {}).get("live") or {}
        known = set((state.get(world) or {}).get("milestones") or {})
        if sv["keys"] is None:
            # First save seen: whatever it already holds happened before
            # skald was watching. Record it as "by then" with no lower
            # bound, so a world with history still shows its milestones --
            # the backups, if there are any, can date them properly later.
            for k in keys - set(live) - known:
                store.put_milestone(db(), world, k, None, done, "save")
        else:
            for k in keys - set(sv["keys"]) - set(live):
                store.put_milestone(db(), world, k, sv["last_ts"], done, "save")
        store.put_save(db(), world, name, done, world_time, sorted(keys), save_version,
                       prev_ts=sv["last_ts"])


def milestone_poller():
    n = 0
    while True:
        for scan, due in ((scan_saves, True), (scan_backups, n % BACKUP_SCAN_EVERY == 0)):
            if due:
                try:
                    scan()
                except Exception as e:
                    print(f"{scan.__name__} failed: {e}", flush=True)
        n += 1
        time.sleep(SAVE_SCAN_SECONDS)


def online_seconds(sessions, lo, hi):
    """Seconds anyone at all was online between lo and hi (no double count)."""
    spans = sorted((s["start"], s["end"] if s["end"] is not None else hi)
                   for s in sessions)
    total, end = 0.0, lo
    for a, b in spans:
        a, b = max(a, lo), min(b, hi)
        if b > max(a, end):
            total += b - max(a, end)
            end = max(end, b)
    return total


def world_clock(h, world, now):
    """The world's own elapsed seconds, as of `now`, or None if unknown.

    The save records it exactly; from there it advances only while someone is
    online -- the server stops the clock when the world empties (312 hourly
    backups bear this out: the clock tracked online time, not wall time).
    """
    sv = load_milestones().get(world, {}).get("save") or {}
    if sv.get("world_time") is None or not sv.get("last_ts"):
        return None
    sessions = [s for s in h["sessions"] if s["world"] == world]
    return sv["world_time"] + online_seconds(sessions, sv["last_ts"], now)


def unlocked_biomes(h, world):
    """The biomes this world has reached, by the bosses it has beaten."""
    st = load_milestones().get(world, {})
    keys = (set(st.get("save", {}).get("keys") or []) | set(st.get("live", {}))
            | set(st.get("milestones", {})))
    keys |= {k["key"] for k in h["keys"] if k["world"] == world}
    return [b for b, needs in BIOME_UNLOCK.items() if needs is None or needs in keys]


def weather_report(h, world, now):
    t = world_clock(h, world, now)
    if t is None:
        return None
    r = dict(weather.report(t), world=world)
    allowed = unlocked_biomes(h, world)
    r["locked"] = len(r["biomes"]) - len(allowed)
    r["biomes"] = [b for b in r["biomes"] if b["biome"] in allowed]
    return r


# Weather glyphs, cut the way runes are: straight strokes only, no curves.
# Drawn rather than typed, because a rune *character* shows as an empty box
# on any device without a runic font.
GLYPHS = {
    # sun wheel
    "Clear": "M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3M3.8 3.8l2 2M10.2 10.2l2 2"
             "M12.2 3.8l-2 2M5.8 10.2l-2 2M8 5.4l2.6 2.6L8 10.6 5.4 8z",
    # flat drifting strokes
    "Misty": "M2 5.5h9M5 8.5h9M2 11.5h9",
    # algiz, the tree, standing in mist
    "DeepForest Mist": "M8 13.5V6.5M8 6.5 4.6 3.1M8 6.5l3.4-3.4M2 11.5h4M10 11.5h4",
    "LightRain": "M2.5 4.5h11M5.8 7.2 4.3 11.4M10.2 7.2 8.7 11.4",
    "Rain": "M2.5 4.5h11M5.2 7.2 3.7 12M8.6 7.2 7.1 12M12 7.2 10.5 12",
    # sowilo, the lightning stroke
    "ThunderStorm": "M2.5 4.5h11M9.8 6.4 5.6 10.6h3L5.2 14.8",
    # hagalaz, the hail rune
    "Snow": "M5 2.5v11M11 2.5v11M5 6.5l6 3",
    "SnowStorm": "M5 2.5v11M11 2.5v11M5 6.5l6 3M1.2 4.5h2.4M12.4 11.5h2.4",
    # the same, over the horizon line the Deep North never rises above
    "Twilight Clear": "M8 2.5v2.5M2.8 8h2.5M10.7 8h2.5M4.6 4.6l1.7 1.7"
                      "M11.4 4.6 9.7 6.3M8 6.2l1.8 1.8L8 9.8 6.2 8zM1.5 13.2h13",
    "Twilight Snow": "M5 2v9M11 2v9M5 5.5l6 3M1.5 13.2h13",
    "Twilight Snowstorm": "M5 2v9M11 2v9M5 5.5l6 3M1.2 4h2.2M12.6 8.5h2.2M1.5 13.2h13",
}
# The single-weather biomes: on the dashboard, and in the forecast's columns.
GLYPHS["SwampRain"] = "M2.5 3.5h11M5.5 6 4 10M10.5 6 9 10M1.5 13h13"
GLYPHS["Ashrain"] = "M2.5 3.5h11M5.5 6 4 10.2M10.5 6 9 10.2M5.8 14.5 8 10.5l2.2 4"
GLYPHS["Darklands dark"] = "M8 14.5V2.5M2 6h12M3 9.5h10M2 13h12"
GLYPHS["Heath clear"] = GLYPHS["Clear"]
PHASE_GLYPHS = {
    "day": GLYPHS["Clear"],
    # half a sun over the horizon, the chevron saying which way it is going
    "dawn": "M5.4 11h5.2M4.4 8.4 2.6 6.6M11.6 8.4l1.8-1.8M1.5 13.2h13"
            "M8 1.8 6.3 3.9M8 1.8l1.7 2.1",
    "dusk": "M5.4 11h5.2M4.4 8.4 2.6 6.6M11.6 8.4l1.8-1.8M1.5 13.2h13"
            "M8 4.2 6.3 2.1M8 4.2l1.7-2.1",
    # a crescent, cut as two strokes rather than filled
    "night": "M10.6 2.4 5.6 8l5 5.6M10.6 2.4 8.4 8l2.2 5.6",
}
# Short column headers; the full name stays in the header's tooltip.
# Biome marks, cut the same way: peaks for the mountain, kenaz-like flames
# for the Ashlands, isa (the ice rune) for the Deep North, waves for the sea.
BIOME_GLYPHS = {
    "Meadows": "M1.5 12.5h13M4 12.5V7.5M4 9 2 7M4 9l2-2M8 12.5V6M8 8 5.8 5.8M8 8l2.2-2.2"
               "M12 12.5V7.5M12 9l-2-2M12 9l2-2",
    "Black Forest": "M1.5 14.5h13M5 14.5V8M5 8 2 4.5M5 8l3-3.5M11 14.5V9M11 9 8.5 6M11 9l3-3",
    "Swamp": "M1.5 6.5h13M1.5 10.5h13M4 14.5V6.5M8 14.5V4.5M12 14.5V6.5M8 4.5 6 2.5M8 4.5l2-2",
    "Mountain": "M1 13.5 6 4l3.2 6M7.4 13.5 11 7l4 6.5M1 13.5h14M4.6 7.2h2.8",
    "Plains": "M1.5 13.5h13M4 13.5V5M4 7 2.2 5.2M4 7l1.8-1.8M4 10.5 2.2 8.7M4 10.5 5.8 8.7"
              "M11 13.5V6M11 8 9.2 6.2M11 8l1.8-1.8",
    "Mistlands": "M3 13.5 8 3l5 10.5M1.5 13.5h13M2 8.5h4M10 8.5h4M4 11h8",
    "Ashlands": "M1.5 14.5h13M5.5 12 8 6.5 10.5 12M8 6.5 7 2 10 4.5 8 6.5",
    "Deep North": "M8 2v12M8 4.5 5.5 2M8 4.5 10.5 2M8 9 5.5 6.5M8 9l2.5-2.5"
                  "M2.5 13.5h11M4.5 11h7",
    "Ocean": "M1.5 5.5 4.5 8.5 7.5 5.5 10.5 8.5 13.5 5.5M1.5 10 4.5 13 7.5 10 10.5 13 13.5 10",
}
# A biome appears once the boss before it is down: the Black Forest after
# Eikthyr, the Swamp after The Elder, and so on. Meadows and Ocean are open
# from the first day. Locked biomes are left off the dashboard and out of the
# forecast rather than spoiling what is ahead.
BIOME_UNLOCK = {
    "Meadows": None, "Black Forest": "defeated_eikthyr", "Ocean": None,
    "Swamp": "defeated_gdking", "Mountain": "defeated_bonemass",
    "Plains": "defeated_dragon", "Mistlands": "defeated_goblinking",
    "Ashlands": "defeated_queen", "Deep North": "defeated_fader",
}

# An arrow the page rotates to the wind's bearing.
ARROW = "M8 14.5V2M8 2 4 6.5M8 2l4 4.5"

# Each distinct path set gets one id; "Heath clear" reuses "Clear"'s.
GLYPH_IDS = list(dict.fromkeys(list(GLYPHS.values()) + list(PHASE_GLYPHS.values())
                                + list(BIOME_GLYPHS.values()) + [ARROW]))
BIOME_SHORT = {"Meadows": "Meadows", "Black Forest": "B. Forest",
               "Mountain": "Mountain", "Plains": "Plains",
               "Deep North": "D. North", "Ocean": "Ocean"}


def glyph_defs():
    """Every glyph once, for the page to reference by id."""
    syms = "".join(
        f'<symbol id="g{i}" viewBox="0 0 16 16"><path d="{d}" fill="none"/></symbol>'
        for i, d in enumerate(GLYPH_IDS))
    return f'<svg class="defs" aria-hidden="true">{syms}</svg>'


def glyph(paths, label, cls="ic"):
    return (f'<svg class="{cls}" role="img" aria-label="{html.escape(label)}">'
            f'<title>{html.escape(label)}</title>'
            f'<use href="#g{GLYPH_IDS.index(paths)}"/></svg>')


def weather_glyph(name, label):
    return glyph(GLYPHS.get(name, GLYPHS["Misty"]), label)


def phase_glyph(phase, cls="ic"):
    return glyph(PHASE_GLYPHS[phase], phase, cls)


def biome_glyph(biome):
    return glyph(BIOME_GLYPHS[biome], biome, "ic bi")


def milestone_label(key):
    return MILESTONES.get(key) or (
        key.replace("defeated_", "").replace("killed", "first ").replace("_", " ")
        .strip().capitalize() + (" defeated" if key.startswith("defeated") else " killed"),
        "other")


def milestones(h):
    """Every milestone, oldest first: when it happened and who was online.

    `earliest`/`latest` bound the moment; they are equal when the server log
    gave the exact time. `source` says which record dated it, `fight_seconds`
    how long after the last boss summon the kill came, when both were logged.
    """
    state = load_milestones()
    worlds = set(state) | {k["world"] for k in h["keys"]}
    out = []
    for world in worlds:
        st = state.get(world, {})
        windows = {}  # key -> [(lo, hi, source)], widest last
        for key, w in st.get("live", {}).items():
            windows.setdefault(key, []).append((w["after"], w["by"], "save"))
        for key, w in st.get("milestones", {}).items():
            lo = w["after"] - AUTOSAVE_SLACK if w["after"] is not None else None
            windows.setdefault(key, []).append((lo, w["by"], "backup"))
        logged = {}
        for k in h["keys"]:
            if k["world"] == world:
                logged.setdefault(k["key"], []).append(k["ts"])
        for key in set(windows) | set(logged):
            wins = windows.get(key, [])
            # The log time must agree with every window: a key the saves or
            # backups already held before it is being re-logged (e.g. on a
            # restart), not set. The first agreeing time is the kill.
            exact = next((ts for ts in sorted(logged.get(key, []))
                          if all((lo is None or ts > lo - EDGE_TOLERANCE)
                                 and ts <= hi + EDGE_TOLERANCE for lo, hi, _ in wins)
                          and not any(lo is None and hi < ts for lo, hi, _ in wins)), None)
            fight = None
            if exact is not None:
                lo = hi = exact
                source = "log"
                starts = [sp["ts"] for sp in h["spawns"] if sp["world"] == world
                          and exact - FIGHT_MAX_SECONDS <= sp["ts"] <= exact]
                fight = round(exact - max(starts)) if starts else None
            else:
                bounded = [w for w in wins if w[0] is not None]
                lo, hi, source = (min(bounded, key=lambda w: w[1] - w[0]) if bounded
                                  else min(wins, key=lambda w: w[1]))
            label, kind = milestone_label(key)
            online = sorted({
                s["player"] for s in h["sessions"]
                if s["world"] == world and s["start"] <= hi
                and (s["end"] is None or s["end"] >= lo)
            }) if lo is not None else []
            out.append({"world": world, "key": key, "label": label, "kind": kind,
                        "earliest": lo, "latest": hi, "source": source,
                        "fight_seconds": fight, "online": online})
    return sorted(out, key=lambda m: m["latest"])


def overlap(s, lo, hi):
    end = s["end"] if s["end"] is not None else hi
    return max(0.0, min(end, hi) - max(s["start"], lo))


def online_now(h, now):
    with LOCK:
        status = dict(STATUS)
    worlds = sorted(set(SERVERS) | {s["world"] for s in h["sessions"]})
    out = []
    for w in worlds:
        players = [
            {"name": s["player"], "since": s["start"], "seconds": now - s["start"]}
            for s in h["sessions"] if s["world"] == w and s["end"] is None
        ]
        st = status.get(w, {})
        count = st.get("count")
        out.append({
            "world": w,
            "up": st.get("up", False),
            "player_count": count,
            "players": sorted(players, key=lambda p: p["since"]),
            # Online per the query but not in the log -- joined before the
            # hook existed, or before a backfill was loaded.
            "unidentified": max(0, count - len(players)) if count is not None else 0,
        })
    return out


def playtime(h, now):
    players = {}

    def row(name):
        return players.setdefault(name, {
            "player": name, "all": 0.0, "sessions": 0, "last_seen": 0.0,
            "online": False, "worlds": set(), "deaths": {"all": 0},
            **{k: 0.0 for k, _ in WINDOWS},
        })

    for s in h["sessions"]:
        p = row(s["player"])
        p["sessions"] += 1
        p["all"] += overlap(s, 0, now)
        for k, secs in WINDOWS:
            p[k] += overlap(s, now - secs, now)
        p["last_seen"] = max(p["last_seen"], s["end"] or now)
        p["online"] = p["online"] or s["end"] is None
        p["worlds"].add(s["world"])
    for d in h["deaths"]:
        dd = row(d["player"])["deaths"]
        dd["all"] += 1
        for k, secs in WINDOWS:
            dd[k] = dd.get(k, 0) + (d["ts"] >= now - secs)
    rows = sorted(players.values(), key=lambda p: (-p["7d"], -p["all"]))
    for p in rows:
        p["worlds"] = sorted(p["worlds"])
        for k, _ in WINDOWS:
            p["deaths"].setdefault(k, 0)
        hours = p["all"] / 3600
        p["deaths_per_10h"] = round(p["deaths"]["all"] / hours * 10, 1) if hours >= 1 else None
        for k in ["all"] + [k for k, _ in WINDOWS]:
            p[k] = round(p[k])
    return rows


def recent(h, now, limit):
    out = []
    for s in reversed(h["sessions"][-limit:]):
        end = s["end"] if s["end"] is not None else now
        died = sum(1 for d in h["deaths"]
                   if d["player"] == s["player"] and d["world"] == s["world"]
                   and s["start"] <= d["ts"] <= end)
        out.append({"world": s["world"], "player": s["player"],
                    "start": s["start"], "end": s["end"],
                    "seconds": round(end - s["start"]), "deaths": died})
    return out


def my_characters(h, steam_id, now):
    """The characters a Steam account has actually played, most played first.

    This is what makes claiming safe on a public instance. A claim is not a
    free-form assertion -- "I am Isein" -- because the log already answers
    it: `Got connection SteamID <id>` is followed by the `Got character
    ZDOID from <name>` of whoever that connection turned out to be, and the
    session replay carries the pairing. So the only characters offered are
    the ones that account has been seen playing, and taking someone else's
    would mean having their Steam account.

    Sessions from before the hook existed can lack a SteamID (a backfilled
    log may start mid-connection), so a character is offered if *any* of its
    sessions carry the account. Their playtime still counts: the totals here
    are the character's, not only the identified part.
    """
    mine, stats = set(), {}
    for s in h["sessions"]:
        if s.get("steamid") == steam_id:
            mine.add(s["player"])
    for s in h["sessions"]:
        if s["player"] not in mine:
            continue
        c = stats.setdefault(s["player"], {
            "name": s["player"], "seconds": 0.0, "sessions": 0,
            "last_seen": 0.0, "worlds": set(), "online": False})
        end = s["end"]
        c["sessions"] += 1
        c["seconds"] += (now if end is None else end) - s["start"]
        c["last_seen"] = max(c["last_seen"], s["start"] if end is None else end)
        c["worlds"].add(s["world"])
        c["online"] = c["online"] or end is None
    for c in stats.values():
        c["worlds"] = sorted(c["worlds"])
    return sorted(stats.values(), key=lambda c: -c["seconds"])


def sync_user(conn, user, h, now):
    """Bring a signed-in player's characters up to date. Returns them.

    Called on both pages a signed-in player can land on, because the log is
    what decides this and it keeps moving: a character played for the first
    time this evening should not need anyone to press anything.
    """
    mine = my_characters(h, user["steam_id"], now)
    user["character"] = store.sync_characters(
        conn, user["steam_id"], [c["name"] for c in mine], now)
    return mine


def daily(h, now, days):
    """Per local calendar day, oldest first: hours played, deaths, areas explored."""
    today = datetime.fromtimestamp(now, LOCAL_TZ).date()
    out = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        lo = datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ).timestamp()
        hi = min(lo + 86400, now)
        out.append({
            "date": day.isoformat(),
            "hours": round(sum(overlap(s, lo, hi) for s in h["sessions"]) / 3600, 2),
            "deaths": sum(1 for d in h["deaths"] if lo <= d["ts"] < hi),
            "explored": sum(1 for e in h["explored"] if lo <= e["ts"] < hi),
        })
    return out


def fmt_dur(secs):
    secs = int(secs)
    if secs < 60:
        return "&mdash;" if secs == 0 else "<1m"
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_n(n):
    return str(n) if n else "&mdash;"


def t(ts, fmt=""):
    """A timestamp the page's script rewrites into the viewer's local time.

    fmt: "" date and time, "w" with the weekday too, "t" the time alone,
    "d" the weekday and date alone.
    """
    iso = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")
    attr = f' data-fmt="{fmt}"' if fmt else ""
    return f'<time data-ts="{int(ts)}"{attr}>{iso}</time>'


def t_badge(lo, hi):
    """Date on one line, time or time window on the next, for a boss badge."""
    if lo is None:
        return f"before<br>{t(hi, 'd')}"
    if lo == hi:
        return f"{t(hi, 'd')}<br>{t(hi, 't')}"
    if (datetime.fromtimestamp(lo, LOCAL_TZ).date()
            != datetime.fromtimestamp(hi, LOCAL_TZ).date()):
        return t_range(lo, hi)
    return f"{t(lo, 'd')}<br>{t(lo, 't')}&ndash;{t(hi, 't')}"


def t_range(lo, hi):
    """'Sep 12, 10:06 PM – 11:36 PM', repeating the date only across midnight."""
    if lo is None:
        return f"before {t(hi, 'w')}"
    if lo == hi:
        return t(hi, "w")
    same_day = (datetime.fromtimestamp(lo, LOCAL_TZ).date()
                == datetime.fromtimestamp(hi, LOCAL_TZ).date())
    return f"{t(lo, 'w')} &ndash; {t(hi, 't' if same_day else 'w')}"


def nice_ticks(vmax):
    """0 plus up to four round tick values covering vmax."""
    vmax = max(vmax, 1)
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if vmax / step <= 4:
            break
    top = -(-vmax // step) * step
    return [i * step for i in range(int(top // step) + 1)]


def bar_chart(title, rows, key, tip, note=""):
    """One single-series column chart as inline SVG. `tip` formats a value."""
    w, ht, pl, pr, pt, pb = 600, 150, 40, 4, 10, 20
    ticks = nice_ticks(max(r[key] for r in rows))
    top = ticks[-1]
    ch = ht - pt - pb
    band = (w - pl - pr) / len(rows)
    bw = min(24, band - 2)  # >= 2px of surface between neighbours
    y = lambda v: pt + ch - v / top * ch
    parts = []
    for tv in ticks:
        parts.append(f'<line class="grid" x1="{pl}" x2="{w - pr}" y1="{y(tv):.1f}" y2="{y(tv):.1f}"/>'
                     f'<text class="ax" x="{pl - 6}" y="{y(tv) + 4:.1f}" text-anchor="end">{tv:g}</text>')
    for i, r in enumerate(rows):
        x = pl + i * band + (band - bw) / 2
        v = r[key]
        if v > 0:
            y0, bh = y(v), ch * v / top
            rad = min(4, bh, bw / 2)
            # Rounded data end, square at the baseline.
            parts.append(
                f'<path class="bar" d="M{x:.1f},{y0 + bh:.1f}V{y0 + rad:.1f}'
                f'Q{x:.1f},{y0:.1f} {x + rad:.1f},{y0:.1f}H{x + bw - rad:.1f}'
                f'Q{x + bw:.1f},{y0:.1f} {x + bw:.1f},{y0 + rad:.1f}V{y0 + bh:.1f}Z"/>')
        d = datetime.fromisoformat(r["date"])
        if (len(rows) - 1 - i) % 5 == 0:  # the last day, then every fifth back
            # The last label hangs off the right edge if centred on its bar.
            end = i == len(rows) - 1
            parts.append(f'<text class="ax" x="{x + bw if end else x + bw / 2:.1f}" y="{ht - 5}" '
                         f'text-anchor="{"end" if end else "middle"}">{d.strftime("%b")} {d.day}</text>')
        # Hit target: the whole column band, not just the bar.
        label = f'{d.strftime("%a %b")} {d.day}: {tip(v)}'
        parts.append(f'<rect class="hit" x="{pl + i * band:.1f}" y="{pt}" width="{band:.1f}" '
                     f'height="{ch}" data-tip="{html.escape(label)}"/>')
    parts.append(f'<line class="base" x1="{pl}" x2="{w - pr}" y1="{pt + ch}" y2="{pt + ch}"/>')
    note = f'<span class="muted">{note}</span>' if note else ""
    return (f'<figure class="chart"><figcaption>{title}{note}</figcaption>'
            f'<svg viewBox="0 0 {w} {ht}" role="img" aria-label="{html.escape(title)}, '
            f'last {len(rows)} days">{"".join(parts)}</svg></figure>')


def status_of(world):
    with LOCK:
        return dict(STATUS.get(world) or {}) or None


def worlds_of(h):
    """Tab order: TRACKER_SERVERS order (the default world first), then any
    world seen only in the logs."""
    extra = sorted({s["world"] for s in h["sessions"]} - set(SERVERS))
    return list(SERVERS) + extra


def default_world(h):
    names = worlds_of(h)
    return DEFAULT_WORLD if DEFAULT_WORLD in names else (names[0] if names else "")


def pick_world(h, requested):
    """The world a request asked for, matched case-insensitively; None if
    it named one that doesn't exist, the default if it named none."""
    if not requested:
        return default_world(h)
    for w in worlds_of(h):
        if w.lower() == requested.lower():
            return w
    return None


# Modifier lines all land in the same second at startup, so anything set
# more than a minute apart belongs to a different run of the server.
MODIFIER_RUN_GAP = 60


def world_settings(h, world):
    """How this world is set up, as of the last server start Skald saw.

    Modifiers are logged once, at startup, and never again -- so the newest
    run's lines are the current truth and older ones are history. A preset
    is logged as itself rather than expanded, so a world can report either
    a preset or a list of settings, and (if someone passes both) both.
    """
    lines = [m for m in h["modifiers"] if m["world"] == world]
    if not lines:
        return None
    run = [lines[-1]]
    for m in reversed(lines[:-1]):
        if run[-1]["ts"] - m["ts"] > MODIFIER_RUN_GAP:
            break
        run.append(m)
    run.reverse()
    preset = next((m["key"] for m in run if m["kind"] == "preset"), None)
    seen, mods = set(), []
    for m in run:
        if m["kind"] != "modifier" or m["key"] in seen:
            continue
        seen.add(m["key"])
        mods.append({"setting": m["key"], "value": m["value"],
                     "setting_label": MODIFIER_LABELS.get(
                         m["key"], m["key"].replace("_", " ").capitalize()),
                     "value_label": MODIFIER_VALUES.get(
                         m["value"], m["value"].replace("_", " "))})
    mods.sort(key=lambda m: (MODIFIER_ORDER.index(m["setting"])
                             if m["setting"] in MODIFIER_ORDER
                             else len(MODIFIER_ORDER), m["setting"]))
    return {"world": world, "since": run[0]["ts"], "preset": preset,
            "modifiers": mods}


def for_world(h, world):
    """History narrowed to one world, in the same shape as history()."""
    return {k: [x for x in h[k] if x["world"] == world]
            for k in ("sessions", "deaths", "explored", "keys", "spawns",
                      "modifiers")}


def for_players(h, names):
    """History narrowed to a set of characters, in the shape history() has.

    `explored`, `keys` and `spawns` are the world's, not a player's, so they
    empty out rather than being attributed to whoever happened to be on --
    which is also why the personal charts are hours and deaths only.
    """
    names = set(names)
    return {"sessions": [x for x in h["sessions"] if x["player"] in names],
            "deaths": [x for x in h["deaths"] if x["player"] in names],
            "explored": [], "keys": [], "spawns": [], "modifiers": []}


def me_stats(h, now, names, primary):
    """Your numbers: the dashboard's own tables, narrowed to your characters.

    Deliberately derived from the same functions the public page uses, so
    there is one definition of an hour played and one of a death, and your
    page cannot drift from the table you appear in.
    """
    mine = for_players(h, names)
    rows = {r["player"]: r for r in playtime(mine, now)}
    me = rows.get(primary)
    if not me:
        return None
    sessions = [x for x in mine["sessions"] if x["player"] == primary]
    longest = max(sessions, key=lambda x: overlap(x, 0, now), default=None)

    # Where you stand, ranked among every character on the server -- the same
    # unit the dashboard's table uses, so the two agree.
    group = playtime(h, now)
    by_hours = sorted(group, key=lambda p: -p["all"])
    dangerous = sorted((p for p in group if p["deaths_per_10h"] is not None),
                       key=lambda p: p["deaths_per_10h"])
    def place(rank_rows):
        names_ = [p["player"] for p in rank_rows]
        return ((names_.index(primary) + 1, len(names_))
                if primary in names_ else None)

    other = sum(r["all"] for n, r in rows.items() if n != primary)
    return {
        "character": primary,
        "windows": [(k, me[k], me["deaths"][k]) for k, _ in WINDOWS],
        "all": me["all"], "deaths": me["deaths"]["all"],
        "deaths_per_10h": me["deaths_per_10h"],
        "sessions": me["sessions"], "online": me["online"],
        "worlds": me["worlds"], "last_seen": me["last_seen"],
        "first_seen": min((x["start"] for x in sessions), default=None),
        "longest": ({"seconds": round(overlap(longest, 0, now)),
                     "start": longest["start"], "world": longest["world"]}
                    if longest else None),
        "rank_hours": place(by_hours), "rank_deaths": place(dangerous),
        "other_seconds": round(other),
        "days": daily(for_players(h, [primary]), now, CHART_DAYS),
        "recent": recent(for_players(h, [primary]), now, 10),
    }


def render_world_settings(settings, status):
    """How this world is set up, in one line -- or an honest blank.

    Three states worth telling apart: settings we have read from the log,
    a world the server says is modified whose startup we did not see, and
    a world running the defaults.
    """
    pills = []
    if settings:
        if settings["preset"]:
            name = settings["preset"]
            pills.append('<span class="pill preset">'
                         f'{html.escape(MODIFIER_VALUES.get(name, name)).capitalize()}'
                         " preset</span>")
        for m in settings["modifiers"]:
            pills.append(f'<span class="pill">{html.escape(m["setting_label"])}'
                         f' <b>{html.escape(m["value_label"])}</b></span>')
    if pills:
        return f'<div class="settings">{"".join(pills)}</div>'
    modified = (status or {}).get("modified")
    if modified:
        # The server says so but we have never seen it say what: the lines
        # are written once, at startup, and we were not watching then.
        return ('<div class="settings"><span class="pill unknown">Modified</span>'
                '<span class="muted">non-default settings; Skald reads which ones'
                ' the next time this server starts</span></div>')
    if modified is False:
        return '<div class="settings"><span class="pill">Default settings</span></div>'
    return ""


def render(h, now, world, user=None):
    status = {w["world"]: w for w in online_now(h, now)}

    # Tabs: one per world, each with its live player count. Plain links, so
    # they work without JS and the minute refresh stays on the same tab.
    tabs = []
    for w in worlds_of(h):
        st = status.get(w, {})
        n = len(st.get("players", [])) + st.get("unidentified", 0)
        dot = '<span class="dot on"></span>' if st.get("up") else '<span class="dot"></span>'
        count = f'<span class="tab-count">{n}</span>' if st.get("up") and n else ""
        cur = ' aria-current="page"' if w == world else ""
        tabs.append(f'<a class="tab" href="/?world={urllib.parse.quote(w)}"{cur}>'
                    f'{dot}{html.escape(w)}{count}</a>')

    st = status.get(world, {"up": False, "players": [], "unidentified": 0})
    if not st["up"]:
        body = '<p class="muted">server offline</p>'
    elif not st["players"] and not st["unidentified"]:
        body = '<p class="muted">nobody online</p>'
    else:
        items = [
            f'<li><b>{html.escape(p["name"])}</b>'
            f'<span class="muted"> since {t(p["since"])} &middot; '
            f'{fmt_dur(p["seconds"])}</span></li>'
            for p in st["players"]
        ]
        if st["unidentified"]:
            items.append(f'<li class="muted">{st["unidentified"]} not yet identified</li>')
        body = "<ul>" + "".join(items) + "</ul>"
    n = len(st["players"]) + st["unidentified"]
    badge = (f'<span class="dot on"></span>{n} online' if st["up"]
             else '<span class="dot"></span>offline')
    card = (f'<section class="card"><h3>Now<span class="badge">{badge}</span></h3>'
            f'{body}</section>')

    wx = weather_report(h, world, now)
    if wx:
        mins = max(1, round(wx["changes_in"] / 60))
        tiles = "".join(
            f'<div class="tile" data-tip="{html.escape(b["biome"])}: then '
            f'{html.escape(b["next"])}, in about {mins}m">'
            f'{weather_glyph(b["weather"], b["label"])}<div class="tt">'
            f'<div class="tb">{biome_glyph(b["biome"])}{html.escape(b["biome"])}</div>'
            f'<div class="tw">{html.escape(b["label"])}</div>'
            f'<div class="tv">wind {round(b["wind"] * 100)}%</div></div></div>'
            for b in wx["biomes"])
        shown = [b["biome"] for b in wx["biomes"]]
        steady = [b for b in weather.CONSTANT if b in shown]
        fc = weather.forecast(wx["world_time"])
        rows, seen, used = [], None, {}
        for r in fc:
            cells = ""
            for b in shown:
                bw = r["biomes"][b]
                used[bw["name"]] = bw["label"]
                cells += (f'<td data-tip="{html.escape(b)}: {html.escape(bw["label"])}">'
                          f'{weather_glyph(bw["name"], bw["label"])}</td>')
            day = f'{r["day"]}' if r["day"] != seen else ""
            seen = r["day"]
            rows.append(
                f'<tr class="{"now " if r["now"] else ""}ph-{r["phase"]}'
                f'{" newday" if day else ""}"><td class="num day">{day}</td>'
                f'<td class="num">{r["clock"]}</td>'
                f'<td data-tip="{r["phase"]}">{phase_glyph(r["phase"])}</td>{cells}'
                f'<td class="num wind">{r["wind_dir"]}'
                f'<span class="muted"> {round(r["wind"] * 100)}</span></td></tr>')
        heads = "".join(
            f'<th title="{html.escape(b)}">{html.escape(BIOME_SHORT.get(b, b))}</th>'
            for b in shown)
        # Two weathers can share a label ("clear"); one entry each is enough.
        by_label = {label: n for n, label in used.items()}
        legend = " ".join(
            f'<span class="leg">{weather_glyph(n, label)}{html.escape(label)}</span>'
            for label, n in sorted(by_label.items()))
        forecast = (
            glyph_defs() +
            '<details class="forecast"><summary>7-day forecast &mdash; the next '
            f'{len(rows)} weather turns, about 3h 30m of play</summary>'
            '<div class="wrap"><table class="fc"><thead><tr><th class="num">Day</th>'
            f'<th class="num">Time</th><th></th>{heads}<th class="num">Wind</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="legend">{legend}</p>'
            + '<p class="muted note">An in-game day is 30 minutes of play, and the weather '
            'turns every 11 minutes of it.'
            # Name only the one-weather biomes this world has actually reached.
            + (f' {html.escape(steady[0])} has one weather, so its column never changes.'
               if len(steady) == 1 else
               f' {html.escape(", ".join(steady))} have one weather each, so their'
               ' columns never change.' if steady else "")
            + '</p></details>')
        weather_card = (
            f'<section class="card weather">'
            f'<div class="sky">'
            f'<div class="sky-now">{phase_glyph(wx["phase"], "ic xl")}'
            f'<div><b>Day {wx["day"]}</b> &middot; {wx["clock"]}'
            f'<div class="muted">{html.escape(wx["next_phase"])} at {wx["next_phase_at"]}'
            f', in about {max(1, round(wx["next_phase_in"] / 60))}m of play</div></div></div>'
            f'<div class="sky-wind"><div>'
            f'<span class="arrow" style="transform:rotate({wx["wind_angle"]:.0f}deg)">'
            f'{glyph(ARROW, "wind direction", "ic lg")}</span></div>'
            f'<div><b>{wx["wind_dir"]}</b><div class="muted">wind, changes in {mins}m</div>'
            f'</div></div></div>'
            f'<div class="tiles">{tiles}</div>'
            + (f'<p class="muted note">{wx["locked"]} more biome'
               f'{"s" if wx["locked"] != 1 else ""} unlock as bosses fall.</p>'
               if wx["locked"] else "")
            + ''
            f'<p class="muted note">Valheim\'s weather and clock follow the world\'s own time, '
            f'which only runs while someone is online; this one is counted on from the last '
            f'autosave.</p>{forecast}</section>')
    else:
        weather_card = ('<section class="card weather"><h3>Weather</h3>'
                        "<p class=\"muted\">unknown until this world's next autosave</p>"
                        '</section>')

    hw = for_world(h, world)
    stats = playtime(hw, now)
    rows, death_rows = [], []
    for p in stats:
        name = html.escape(p["player"])
        if p["online"]:
            name += ' <span class="dot on" title="online"></span>'
        rows.append(
            f'<tr><td>{name}</td>'
            + "".join(f'<td class="num">{fmt_dur(p[k])}</td>' for k, _ in WINDOWS)
            + f'<td class="num">{fmt_dur(p["all"])}</td>'
            f'<td>{"online now" if p["online"] else t(p["last_seen"])}</td></tr>')
    for p in sorted(stats, key=lambda p: (-p["deaths"]["all"], p["player"])):
        rate = p["deaths_per_10h"]
        death_rows.append(
            f'<tr><td>{html.escape(p["player"])}</td>'
            + "".join(f'<td class="num">{fmt_n(p["deaths"][k])}</td>' for k, _ in WINDOWS)
            + f'<td class="num">{fmt_n(p["deaths"]["all"])}</td>'
            f'<td class="num">{rate if rate is not None else "&mdash;"}</td></tr>')

    recent_rows = [
        f'<tr><td>{html.escape(r["player"])}</td>'
        f'<td>{t(r["start"])}</td>'
        f'<td>{t(r["end"]) if r["end"] is not None else "online"}</td>'
        f'<td class="num">{fmt_dur(r["seconds"])}</td>'
        f'<td class="num">{fmt_n(r["deaths"])}</td></tr>'
        for r in recent(hw, now, 50)
    ]

    days = daily(hw, now, CHART_DAYS)
    quiet = not any(r["hours"] or r["deaths"] or r["explored"] for r in days)
    quiet_note = f'<p class="muted">No play on {html.escape(world)} in the last {CHART_DAYS} days.</p>'
    charts = quiet_note if quiet else "".join([
        bar_chart("Player-hours per day", days, "hours",
                  lambda v: fmt_dur(v * 3600).replace("&mdash;", "none"),
                  "Everyone's time added together, so two players on for an hour count 2h."),
        bar_chart("Deaths per day", days, "deaths",
                  lambda v: f"{v} death{'s' if v != 1 else ''}"),
        bar_chart("New landmarks per day", days, "explored",
                  lambda v: f"{v} new landmark{'s' if v != 1 else ''}",
                  "Crypts, ruins, camps and the like, placed by the server as players "
                  "reach new ground: a measure of exploration pace."),
    ])
    day_rows = [
        f'<tr><td>{r["date"]}</td><td class="num">{fmt_dur(r["hours"] * 3600)}</td>'
        f'<td class="num">{fmt_n(r["deaths"])}</td><td class="num">{fmt_n(r["explored"])}</td></tr>'
        for r in reversed(days)
    ]

    ms = [m for m in milestones(h) if m["world"] == world]
    got = {m["key"]: m for m in ms if m["kind"] == "boss"}
    badges = []
    for i, (key, name) in enumerate(BOSSES):
        m = got.get(key)
        if m:
            party = html.escape(", ".join(m["online"]))
            badges.append(
                f'<div class="trophy"><div class="medal">{NUMERALS[i]}</div>'
                f'<div class="boss">{html.escape(name)}</div>'
                f'<div class="when">{t_badge(m["earliest"], m["latest"])}</div>'
            + (f'<div class="fight">{fmt_dur(m["fight_seconds"])} fight</div>'
               if m["fight_seconds"] else "")
            + (f'<div class="party">{party}</div>' if party else "") + '</div>')
        else:
            # No spoilers: the name isn't anywhere in the page, not even in
            # an attribute, until the boss is beaten.
            badges.append(
                f'<div class="trophy locked"><div class="medal">{NUMERALS[i]}</div>'
                f'<div class="boss" aria-label="Unknown boss">???</div>'
                f'<div class="when">not yet slain</div></div>')
    ms_rows = [
        f'<tr><td><span class="kind kind-{m["kind"]}">{html.escape(m["kind"])}</span> '
        f'{html.escape(m["label"])}</td>'
        f'<td>{t_range(m["earliest"], m["latest"])}</td>'
        f'<td>{html.escape(", ".join(m["online"])) or "&mdash;"}</td></tr>'
        for m in ms if m["kind"] != "boss"
    ]

    q = f"?world={urllib.parse.quote(world)}"
    empty = lambda n: f'<tr><td colspan="{n}" class="muted">nothing recorded yet</td></tr>'
    return (PAGE
            .replace("__TITLE__", html.escape(world))
            .replace("__TABS__", "".join(tabs))
            .replace("__CARD__", card)
            .replace("__WORLD__", render_world_settings(
                world_settings(h, world), status.get(world)))
            .replace("__WEATHER__", weather_card)
            .replace("__TROPHIES__", f'<div class="trophies">{"".join(badges)}</div>')
            .replace("__MILESTONES__", "\n".join(ms_rows) or empty(3))
            .replace("__PLAYTIME__", "\n".join(rows) or empty(6))
            .replace("__DEATHS__", "\n".join(death_rows) or empty(6))
            .replace("__CHARTS__", charts)
            .replace("__DAILY__", "\n".join(day_rows))
            .replace("__RECENT__", "\n".join(recent_rows) or empty(5))
            .replace("__Q__", q)
            .replace("__WHO__", sign_in_widget(user))
            .replace("__TS__", t(now)))


# Tokens (__CARD__ etc.) are filled by str.replace so the inline CSS/JS
# braces don't need escaping.
PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>__TITLE__ &middot; Skald</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Averia+Serif+Libre:wght@400;700&family=Cinzel:wght@600;
  700&display=swap" rel="stylesheet">
<meta http-equiv="refresh" content="60">
<style>
 /* Valheim's in-game look: dark wood-and-stone panels, bronze trim, gold
    headings, parchment text. Dark in both color schemes, like the game. */
 :root{color-scheme:dark;
  --bg:#0e0b08;--panel:#1c1610;--panel-2:#241c14;--line:#3b2e20;--bronze:#8a6a3f;
  --gold:#e8b25a;--gold-dim:#cfa266;--fg:#eadcc0;--muted:#a8977a;--on:#8fc46a;
  --series-1:#c9822e;
  --display:'Cinzel',Georgia,serif;--body:'Averia Serif Libre',Georgia,serif}
 body{font:15px/1.5 var(--body);margin:0 auto;max-width:62rem;padding:1.75rem 1rem 3rem;color:var(--fg);
  background:radial-gradient(ellipse 120% 60% at 50% 0%,#2a2016 0%,var(--bg) 70%) fixed,var(--bg)}
 a{color:var(--gold)} a:hover{color:#f6cf86}
 h1{font:700 1.9rem/1.1 var(--display);color:var(--gold);letter-spacing:.06em;text-align:center;margin:0;
  text-shadow:0 1px 0 #000,0 0 18px rgba(232,178,90,.25)}
 .divider{display:block;margin:.5rem auto .4rem;width:min(22rem,80%);height:14px}
 h2{font:600 1.1rem/1.2 var(--display);color:var(--gold);letter-spacing:.05em;margin:2.1rem 0 .7rem;
  display:flex;align-items:center;gap:.75rem}
 h2::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--bronze),transparent)}
 h3{font:600 .95rem var(--display);margin:0 0 .4rem;display:flex;justify-content:space-between;gap:1rem;
  color:var(--gold-dim);letter-spacing:.03em}
 h3.world{margin:1rem 0 .5rem;font-size:.85rem;color:var(--muted)}
 /* How the world is set up: one quiet line of pills under the card. */
 .settings{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem;margin:.6rem 0 0;
  font-size:.8rem;color:var(--muted)}
 .pill{border:1px solid var(--line);border-radius:999px;padding:.1rem .6rem;
  background:var(--panel);color:var(--muted)}
 .pill b{color:var(--gold-dim);font-weight:400}
 .pill.preset{border-color:var(--bronze);color:var(--gold-dim)}
 .pill.unknown{border-color:var(--bronze);color:var(--gold-dim)}
 .meta,.muted{color:var(--muted)} .meta{text-align:center;font-size:.85rem;margin-bottom:1.5rem}
 /* Sign-in: one line of chrome, and only when it is configured. */
 .who{display:flex;justify-content:center;align-items:center;gap:.6rem;margin:-1rem 0 1.2rem;
  font-size:.82rem;color:var(--muted)}
 .who b{color:var(--gold-dim);font-weight:400}
 .who button,.signin{font:inherit;color:var(--gold-dim);background:none;cursor:pointer;
  border:1px solid var(--line);border-radius:3px;padding:.15rem .6rem;text-decoration:none}
 .who button:hover,.signin:hover{border-color:var(--bronze);color:var(--gold)}
 a.signin{display:block;width:fit-content;margin:-1rem auto 1.2rem}
 .who a.signin{display:inline-block;margin:0}
 .panel,.card,table,.chart,.trophy{background:linear-gradient(180deg,#211a12,var(--panel));border:1px solid var(--line);
  border-radius:3px;box-shadow:inset 0 0 0 1px rgba(232,178,90,.05),0 2px 10px rgba(0,0,0,.45)}
 /* World tabs: carved-plank look, gold when selected. */
 .tabs{display:flex;gap:.35rem;border-bottom:1px solid var(--bronze);margin-bottom:1.1rem;overflow-x:auto;
  scrollbar-width:none}
 .tab{display:flex;align-items:center;gap:.1rem;padding:.55rem 1rem .5rem;font:600 .95rem var(--display);
  letter-spacing:.05em;
  color:var(--muted);text-decoration:none;border:1px solid transparent;border-bottom:0;border-radius:3px 3px 0 0;
  white-space:nowrap;margin-bottom:-1px}
 .tab:hover{color:var(--gold-dim);background:rgba(232,178,90,.04)}
 .tab[aria-current="page"]{color:var(--gold);background:linear-gradient(180deg,#2a2016,var(--bg));
  border-color:var(--bronze);
  box-shadow:inset 0 2px 0 var(--gold)}
 .tab-count{font:400 .72rem var(--body);background:var(--line);color:var(--fg);border-radius:9px;padding:0 .4rem;
  margin-left:.45rem}
 .card{padding:.8rem .95rem}
 .card ul{margin:0;padding:0;list-style:none} .card li{padding:.15rem 0} .card p{margin:0}
 /* Weather dashboard: a sky strip, then one tile per biome. */
 .weather{margin-top:.75rem;padding:.9rem 1rem 1rem}
 .sky{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:1rem;
  padding-bottom:.8rem;margin-bottom:.9rem;border-bottom:1px solid var(--line)}
 .sky-now,.sky-wind{display:flex;align-items:center;gap:.7rem}
 .sky-now b,.sky-wind b{font:700 1.05rem var(--display);color:var(--gold);letter-spacing:.04em}
 .sky .muted{font-size:.8rem}
 .arrow{display:inline-block;color:var(--gold-dim)}
 .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(8.6rem,1fr));gap:.55rem}
 .tt{display:flex;flex-direction:column;gap:.1rem}
 .tile{display:flex;flex-direction:column;align-items:center;gap:.15rem;text-align:center;
  padding:.65rem .4rem .55rem;border:1px solid var(--line);border-radius:3px;
  background:linear-gradient(180deg,rgba(232,178,90,.035),transparent)}
 .tile:hover{border-color:var(--bronze)}
 .tile .ic{color:var(--gold);margin:.15rem 0}
 .tb{display:flex;align-items:center;gap:.3rem;font:600 .72rem var(--display);
  color:var(--gold-dim);letter-spacing:.05em;text-transform:uppercase}
 .tb .ic{color:var(--bronze);margin:0}
 .tw{font-size:.88rem} .tv{font-size:.72rem;color:var(--muted);font-variant-numeric:tabular-nums}
 /* Once there is room, tiles run wide with the glyph beside the text. */
 @media (min-width:52rem){.tiles{grid-template-columns:repeat(3,1fr);gap:.6rem}
  .tile{flex-direction:row;justify-content:flex-start;text-align:left;gap:.8rem;padding:.75rem .9rem}
  .tile .ic{width:34px;height:34px;flex:0 0 34px;margin:0}
  .tw{font-size:.95rem}}
 .weather .note{margin-top:.5rem}
 .phase{font:600 .68rem var(--display);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  border:1px solid var(--line);border-radius:2px;padding:0 .35rem;margin-right:.35rem}
 .ph-night .phase,.phase-line .phase{color:var(--gold-dim);border-color:var(--bronze)}
 .forecast tbody tr.now td{background:rgba(232,178,90,.07)}
 .forecast tbody tr.ph-night td{color:var(--muted)}
 .forecast summary{color:var(--gold-dim);font:600 .85rem var(--display);letter-spacing:.03em}
 /* Compact icon grid: the glyphs carry the weather, the tooltip names it. */
 .defs{display:none}
 .ic{width:15px;height:15px;stroke:currentColor;stroke-width:1.5;stroke-linecap:square;vertical-align:-3px}
 .ic.bi{width:13px;height:13px;stroke-width:1.3;vertical-align:-2px}
 .ic.lg{width:26px;height:26px;stroke-width:1.4;vertical-align:-6px}
 .ic.xl{width:34px;height:34px;stroke-width:1.3;vertical-align:-9px;color:var(--gold)}
 .tile .ic{width:30px;height:30px;stroke-width:1.4}
 .fc{font-size:.8rem} .fc th,.fc td{padding:.16rem .45rem;text-align:center}
 .fc th{font-size:.66rem;padding:.3rem .45rem}
 .fc th.num,.fc td.num{text-align:right} .fc td.day{color:var(--gold-dim);font-weight:700}
 .fc tbody tr.newday td{border-top:1px solid var(--bronze)}
 .fc td.wind{font-variant-numeric:tabular-nums;white-space:nowrap}
 .fc tbody tr:hover td{background:rgba(232,178,90,.05)}
 .legend{display:flex;flex-wrap:wrap;gap:.2rem .9rem;margin-top:.5rem;font-size:.76rem;color:var(--muted)}
 .leg{display:inline-flex;align-items:center;gap:.25rem}
 .badge{font:400 .85rem var(--body);color:var(--muted);white-space:nowrap;letter-spacing:0}
 .dot{display:inline-block;width:.55rem;height:.55rem;border-radius:50%;background:#5d5040;margin-right:.35rem;
  vertical-align:.05rem}
 .dot.on{background:var(--on);box-shadow:0 0 6px rgba(143,196,106,.6)}
 /* Boss achievements: lit gold medallions, dim and locked until earned. */
 .trophies{display:grid;grid-template-columns:repeat(auto-fill,minmax(7.4rem,1fr));gap:.55rem}
 .trophy{padding:.9rem .6rem .8rem;text-align:center;display:flex;flex-direction:column;align-items:center;gap:.25rem}
 .medal{width:3.3rem;height:3.3rem;border-radius:50%;display:grid;place-items:center;margin-bottom:.3rem;
  font:700 1.05rem var(--display);color:#2a1a08;border:2px solid var(--gold);
  background:radial-gradient(circle at 35% 30%,#f7d98f,#c48b35 55%,#6b4516);
  box-shadow:0 0 16px rgba(232,178,90,.35),inset 0 -3px 6px rgba(0,0,0,.35)}
 .trophy .boss{font:700 .95rem var(--display);color:var(--gold);letter-spacing:.03em}
 .trophy .when{font-size:.78rem;color:var(--fg)} .trophy .party{font-size:.74rem;color:var(--muted)}
 .trophy .fight{font:600 .7rem var(--display);color:var(--gold-dim);letter-spacing:.05em;text-transform:uppercase}
 .trophy.locked{background:#15110c;box-shadow:none}
 .trophy.locked .medal{background:#211b14;border-color:#4a3d2d;color:#6f6150;box-shadow:none}
 .trophy.locked .boss{color:#948469;letter-spacing:.2em} .trophy.locked .when{color:#857760;font-style:italic}
 .wrap{overflow-x:auto}
 table{border-collapse:collapse;width:100%}
 th,td{text-align:left;padding:.45rem .65rem;border-bottom:1px solid var(--line);white-space:nowrap}
 tr:last-child td{border-bottom:0}
 th{background:var(--panel-2);font:600 .76rem var(--display);color:var(--gold-dim);letter-spacing:.06em;
  text-transform:uppercase}
 tbody tr:hover td{background:rgba(232,178,90,.04)}
 td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
 .kind{display:inline-block;font:600 .66rem var(--display);padding:.05rem .4rem;border-radius:2px;
  border:1px solid var(--line);
  color:var(--muted);margin-right:.4rem;text-transform:uppercase;letter-spacing:.06em;vertical-align:.1rem}
 .kind-mini-boss{border-color:var(--bronze);color:var(--gold-dim)}
 .note{font-size:.8rem;margin-top:.5rem}
 .charts{display:grid;gap:.75rem}
 .chart{margin:0;padding:.7rem .8rem .35rem}
 .chart figcaption{font:600 .9rem var(--display);color:var(--gold-dim);letter-spacing:.03em;margin-bottom:.2rem}
 .chart figcaption span{display:block;font:400 .8rem var(--body);color:var(--muted);letter-spacing:0}
 .chart svg{display:block;width:100%;height:auto}
 .chart .bar{fill:var(--series-1)} .chart .grid{stroke:var(--line);stroke-width:1}
 .chart .base{stroke:var(--bronze);stroke-width:1} .chart .ax{fill:var(--muted);font-size:10px;font-family:var(--body)}
 /* The SVG scales with its box; keep axis text legible at phone width. */
 @media (max-width:640px){.chart .ax{font-size:19px} h1{font-size:1.5rem}
  .tabs{gap:.15rem} .tab{padding:.5rem .55rem .45rem;font-size:.8rem;
  letter-spacing:.03em} .tab-count{margin-left:.3rem}}
 .chart .hit{fill:transparent} .chart .hit:hover{fill:var(--gold);fill-opacity:.07}
 #tip{position:fixed;pointer-events:none;background:#0b0907;color:var(--fg);border:1px solid var(--bronze);
  font-size:12px;
  padding:.25rem .55rem;border-radius:2px;display:none;white-space:nowrap}
 details{margin-top:.6rem} summary{cursor:pointer;color:var(--muted)}
 details table{margin-top:.5rem}
</style></head><body>
<h1>Skald</h1>
<svg class="divider" viewBox="0 0 352 14" aria-hidden="true">
 <line x1="0" y1="7" x2="150" y2="7" stroke="#8a6a3f"/><line x1="202" y1="7" x2="352" y2="7" stroke="#8a6a3f"/>
 <path d="M176 1 L182 7 L176 13 L170 7 Z" fill="#e8b25a"/>
 <path d="M160 7 L164 3 L168 7 L164 11 Z M184 7 L188 3 L192 7 L188 11 Z" fill="#8a6a3f"/>
</svg>
<div class="meta">updated __TS__ &middot; refreshes every minute &middot;
 <a href="/api/online__Q__">online</a> &middot; <a href="/api/playtime__Q__">playtime</a> &middot;
 <a href="/api/daily__Q__">daily</a> JSON &middot; <a href="/diagnostics">diagnostics</a></div>
__WHO__
<nav class="tabs" aria-label="Worlds">__TABS__</nav>
__CARD__
__WORLD__
__WEATHER__
<h2>Bosses slain</h2>
__TROPHIES__
<h2>Other milestones</h2>
<div class="wrap"><table><thead><tr>
 <th>Milestone</th><th>When</th><th>Online at the time</th>
</tr></thead><tbody>
__MILESTONES__
</tbody></table></div>
<p class="muted note">Kills the server logged show their exact time, and how long after the boss
 was summoned it fell. Earlier ones are dated from the world's autosaves (a 30-minute window) or, before
 those were watched, its hourly backups (about 90 minutes).</p>
<h2>Playtime</h2>
<div class="wrap"><table><thead><tr>
 <th>Player</th><th class="num">24h</th><th class="num">7 days</th><th class="num">30 days</th>
 <th class="num">All time</th><th>Last seen</th>
</tr></thead><tbody>
__PLAYTIME__
</tbody></table></div>
<h2>Deaths</h2>
<div class="wrap"><table><thead><tr>
 <th>Player</th><th class="num">24h</th><th class="num">7 days</th><th class="num">30 days</th>
 <th class="num">All time</th><th class="num">Per 10h played</th>
</tr></thead><tbody>
__DEATHS__
</tbody></table></div>
<h2>Last 30 days</h2>
<div class="charts">
__CHARTS__
</div>
<details><summary>Show as a table</summary>
<div class="wrap"><table><thead><tr>
 <th>Date</th><th class="num">Played</th><th class="num">Deaths</th><th class="num">New landmarks</th>
</tr></thead><tbody>
__DAILY__
</tbody></table></div></details>
<h2>Recent sessions</h2>
<div class="wrap"><table><thead><tr>
 <th>Player</th><th>Joined</th><th>Left</th><th class="num">Length</th><th class="num">Deaths</th>
</tr></thead><tbody>
__RECENT__
</tbody></table></div>
<div id="tip"></div>
<script>
 const o={month:'short',day:'numeric',hour:'numeric',minute:'2-digit'};
 const fmts={'':new Intl.DateTimeFormat(undefined,o),w:new Intl.DateTimeFormat(undefined,{weekday:'short',...o}),
  t:new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit'}),
  d:new Intl.DateTimeFormat(undefined,{weekday:'short',month:'short',day:'numeric'})};
 for(const el of document.querySelectorAll('time[data-ts]'))
  el.textContent=(fmts[el.dataset.fmt||'']).format(new Date(el.dataset.ts*1000));
 const tip=document.getElementById('tip');
 document.addEventListener('pointermove',e=>{
  const el=e.target.closest&&e.target.closest('[data-tip]');
  if(!el){tip.style.display='none';return;}
  tip.textContent=el.dataset.tip; tip.style.display='block';
  const x=Math.min(e.clientX+12,innerWidth-tip.offsetWidth-8);
  tip.style.left=x+'px'; tip.style.top=(e.clientY-34)+'px';});
</script>
</body></html>"""


def path_info(path, want_write=False):
    """Whether Skald can actually use a path, which is most of diagnosing it."""
    info = {"path": path, "exists": os.path.isdir(path),
            "readable": os.access(path, os.R_OK | os.X_OK)}
    if want_write:
        info["writable"] = os.access(path, os.W_OK)
    return info


def diagnostics(h, now):
    """What Skald can see, per world: the answer to "why is X missing?"."""
    with LOCK:
        status = dict(STATUS)
    state = load_milestones()
    worlds = []
    for name in worlds_of(h):
        log_file = CONFIG.world(name).log_file
        events = log_file or os.path.join(EVENTS_DIR, f"{name}.log")
        evs = [e for e in h["sessions"] if e["world"] == name]
        st = status.get(name, {})
        save = state.get(name, {}).get("save") or {}
        game_version = st.get("game_version")
        backups = sorted(glob.glob(os.path.join(CONFIG.backups_dir(name), "worlds-*.zip")))
        clock = world_clock(h, name, now)
        worlds.append({
            "world": name,
            "status_url": CONFIG.world(name).status_url,
            "status": ("up" if st.get("up") else "down" if st else "not polled yet"),
            "status_error": st.get("error"),
            # isfile, not exists: a bind mount whose source has gone (a
            # container log path changes when the container is recreated)
            # leaves an empty *directory* behind, which is not a log.
            "events_file": {"path": events, "exists": os.path.isfile(events),
                            "source": "server log" if log_file else "log hook",
                            "sessions_seen": len(evs)},
            "game_version": game_version,
            "game_version_verified": (game_version or "").startswith(VERIFIED_GAME_VERSIONS),
            "saves": dict(path_info(CONFIG.saves_dir(name)),
                          latest=save.get("last") or None,
                          # The gap between the last two saves: the precision
                          # every boss kill without a logged key is dated to.
                          interval=(round(save["last_ts"] - save["prev_ts"])
                                    if save.get("prev_ts") and save.get("last_ts")
                                    else None),
                          version=save.get("version"),
                          version_known=save.get("version") in KNOWN_SAVE_VERSIONS
                          if save.get("version") else None,
                          world_clock=round(clock) if clock is not None else None),
            "backups": dict(path_info(CONFIG.backups_dir(name)), count=len(backups),
                            newest=os.path.basename(backups[-1]) if backups else None),
            "milestones": len([m for m in milestones(h) if m["world"] == name]),
            "biomes_unlocked": len(unlocked_biomes(h, name)),
        })
    counts = db().execute("SELECT count(*) AS events,"
                          " count(DISTINCT world) AS worlds FROM events").fetchone()
    db_path = os.path.join(DATA_DIR, "skald.db")
    return {
        "version": __version__,
        "database": {"path": db_path,
                     "size_bytes": os.path.getsize(db_path) if os.path.exists(db_path) else 0,
                     "events": counts["events"], "worlds": counts["worlds"]},
        "config": {"path": CONFIG.path or "none (environment and defaults only)",
                   "timezone": CONFIG.timezone, "sources": CONFIG.sources},
        "paths": {"events": path_info(EVENTS_DIR, want_write=True),
                  "data": path_info(DATA_DIR, want_write=True),
                  "saves_root": path_info(SAVES_ROOT),
                  "backups_root": path_info(BACKUPS_ROOT)},
        "worlds": worlds,
        "compatibility": {
            "save_versions_known": list(KNOWN_SAVE_VERSIONS),
            "game_versions_verified": list(VERIFIED_GAME_VERSIONS),
            # Lines carrying the game's timestamp that matched no pattern
            # Skald knows. A Valheim update that rewords one shows up here
            # rather than as quietly missing data.
            "unrecognised_lines": sum(store.skipped_lines(db()).values()),
        },
    }


DASH = '<span class="muted">&mdash;</span>'


def yes_no(ok, good="yes", bad="no"):
    cls = "ok" if ok else "bad"
    return f'<span class="{cls}">{good if ok else bad}</span>'


def render_me(user, chars, rows_db, stats=None):
    """Your page: your numbers, your characters, your last few evenings."""
    state = {r["name"]: r for r in rows_db}
    primary = next((r["name"] for r in rows_db if r["is_primary"]), None)
    chosen = bool(primary and state[primary]["chosen"])
    rows = []
    for c in chars:
        st = state.get(c["name"], {})
        if st.get("hidden"):
            action = _button("/me/mine", c["name"], "mine after all")
            note = ' class="off"'
        elif c["name"] == primary:
            action = '<span class="badge">primary</span>'
            note = ""
        else:
            action = (_button("/me/primary", c["name"], "make primary")
                      + _button("/me/hide", c["name"], "not mine"))
            note = ""
        seen = ('<span class="on">online now</span>' if c["online"]
                else t(c["last_seen"]))
        rows.append(
            f'<tr{note}><td><b>{html.escape(c["name"])}</b></td>'
            f'<td class="muted">{html.escape(", ".join(c["worlds"]))}</td>'
            f'<td>{fmt_dur(c["seconds"])}</td><td>{c["sessions"]}</td>'
            f'<td>{seen}</td><td class="act">{action}</td></tr>')

    who = html.escape(user["display_name"] or f'Steam {user["steam_id"][-4:]}')
    avatar = (f'<img class="avatar" src="{html.escape(user["avatar"], quote=True)}" alt="">'
              if user.get("avatar") else "")
    if not rows:
        body = ('<p class="empty">Skald has not seen this Steam account playing yet.'
                ' Characters appear here by themselves once you have joined a world it'
                ' is watching &mdash; it learns which are yours from the server&rsquo;s'
                ' own log, which is why there is nothing here to type in.</p>')
    else:
        body = ('<div class="wrap"><table><thead><tr><th>Character</th><th>Worlds</th>'
                '<th>Played</th><th>Sessions</th><th>Last seen</th><th></th></tr>'
                f'</thead><tbody>{"".join(rows)}</tbody></table></div>')
    if not primary:
        lead = ""
    elif chosen:
        lead = (f'You are <b>{html.escape(primary)}</b>, because you said so. '
                'Skald will not move it now, however much you play the others.')
    else:
        lead = (f'You are <b>{html.escape(primary)}</b> &mdash; your most-played '
                'character, picked automatically. Choose one yourself and it stays put.')
    return (ME_PAGE.replace("__WHO__", f"{avatar}<span>{who}</span>")
            .replace("__STATS__", render_my_stats(stats))
            .replace("__BODY__", body)
            .replace("__LEAD__", f'<p class="note">{lead}</p>' if lead else "")
            .replace("__SESSIONS__", render_my_sessions(stats)))


def ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def stat(value, label):
    return f'<div class="stat"><div class="v">{value}</div><div class="k">{label}</div></div>'


def render_my_stats(st):
    """The headline: what you have done, and where that puts you."""
    if not st:
        return ""
    cells = [stat(fmt_dur(secs), label) for label, secs in
             [("last 24 hours", st["windows"][0][1]),
              ("last 7 days", st["windows"][1][1]),
              ("last 30 days", st["windows"][2][1]),
              ("all time", st["all"])]]
    deaths = st["deaths"]
    per10 = "&mdash;" if st["deaths_per_10h"] is None else f'{st["deaths_per_10h"]:g}'
    cells.append(stat(fmt_n(deaths), "death" if deaths == 1 else "deaths"))
    cells.append(stat(per10, "deaths / 10h"))

    plural = "" if st["sessions"] == 1 else "s"
    facts = [f'<b>{st["sessions"]}</b> session{plural}']
    if st["first_seen"]:
        facts.append(f'first seen {t(st["first_seen"], "d")}')
    if st["longest"] and st["longest"]["seconds"] >= 60:
        facts.append(f'longest {fmt_dur(st["longest"]["seconds"])} '
                     f'on {t(st["longest"]["start"], "d")}')
    if st["worlds"]:
        facts.append("on " + html.escape(", ".join(st["worlds"])))
    if st["other_seconds"] >= 60:
        facts.append(f'{fmt_dur(st["other_seconds"])} more as your other characters')

    ranks = []
    if st["rank_hours"]:
        n, of = st["rank_hours"]
        ranks.append(f'<b>{ordinal(n)}</b> of {of} by hours played')
    if st["rank_deaths"]:
        # Ascending, so first is the most careful. Said plainly rather than
        # ranked as "best": dying a lot is not losing at Valheim.
        n, of = st["rank_deaths"]
        ranks.append(f'<b>{ordinal(n)}</b> of {of} fewest deaths per hour')

    charts = ""
    if any(d["hours"] or d["deaths"] for d in st["days"]):
        charts = (bar_chart("Your hours per day", st["days"], "hours",
                            lambda v: fmt_dur(v * 3600).replace("&mdash;", "none"))
                  + bar_chart("Your deaths per day", st["days"], "deaths",
                              lambda v: f"{v} death{'s' if v != 1 else ''}"))
    online = ' <span class="on">online now</span>' if st["online"] else ""
    rank_line = f'<p class="facts">{" &middot; ".join(ranks)}</p>' if ranks else ""
    return (f'<h2>{html.escape(st["character"])}{online}</h2>'
            f'<div class="stats">{"".join(cells)}</div>'
            f'<p class="facts">{" &middot; ".join(facts)}</p>{rank_line}{charts}')


def render_my_sessions(st):
    if not st or not st["recent"]:
        return ""
    ONLINE = '<span class="on">online</span>'
    rows = "".join(
        f'<tr><td>{html.escape(r["world"])}</td><td>{t(r["start"])}</td>'
        f'<td>{t(r["end"]) if r["end"] is not None else ONLINE}</td>'
        f'<td>{fmt_dur(r["seconds"])}</td><td>{fmt_n(r["deaths"])}</td></tr>'
        for r in st["recent"])
    return ('<h2>Your last few</h2><div class="wrap"><table><thead><tr><th>World</th>'
            '<th>From</th><th>To</th><th>Played</th><th>Deaths</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def _button(action, name, label):
    return (f'<form method="post" action="{action}">'
            f'<input type="hidden" name="name" value="{html.escape(name, quote=True)}">'
            f'<button type="submit">{label}</button></form>')


ME_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>You &middot; Skald</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Averia+Serif+Libre:wght@400;700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&display=swap" rel="stylesheet">
<style>
 :root{color-scheme:dark;--bg:#0e0b08;--panel:#1c1610;--panel-2:#241c14;--line:#3b2e20;
  --bronze:#8a6a3f;--gold:#e8b25a;--gold-dim:#cfa266;--fg:#eadcc0;--muted:#a8977a;--on:#8fc46a}
 body{font:15px/1.5 'Averia Serif Libre',Georgia,serif;margin:0 auto;max-width:50rem;
  padding:1.75rem 1rem 3rem;color:var(--fg);background:var(--bg)}
 h1{font:700 1.6rem 'Cinzel',Georgia,serif;color:var(--gold);letter-spacing:.06em;margin:0 0 .2rem}
 a{color:var(--gold)} .muted{color:var(--muted)} .on{color:var(--on)}
 .who{display:flex;align-items:center;gap:.5rem;margin:.1rem 0 1.4rem;color:var(--muted);font-size:.9rem}
 .avatar{width:1.6rem;height:1.6rem;border-radius:3px;border:1px solid var(--line)}
 .wrap{overflow-x:auto}
 table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);border-radius:3px}
 th,td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid var(--line);white-space:nowrap}
 tr:last-child td{border-bottom:0}
 th{background:var(--panel-2);font:600 .72rem 'Cinzel',Georgia,serif;color:var(--gold-dim);
  letter-spacing:.06em;text-transform:uppercase}
 td.act{text-align:right} td.act form{display:inline}
 button{font:inherit;font-size:.82rem;color:var(--gold-dim);background:none;cursor:pointer;
  border:1px solid var(--line);border-radius:3px;padding:.1rem .55rem;margin-left:.3rem}
 button:hover{border-color:var(--bronze);color:var(--gold)}
 .badge{font:600 .72rem 'Cinzel',Georgia,serif;color:var(--gold);letter-spacing:.06em;
  text-transform:uppercase;border:1px solid var(--bronze);border-radius:3px;padding:.1rem .5rem}
 .empty{background:var(--panel);border:1px solid var(--line);border-radius:3px;padding:.9rem 1rem;
  color:var(--muted)}
 tr.off td{opacity:.45}
 p.note{color:var(--muted);font-size:.85rem}
 h2{font:600 1.05rem 'Cinzel',Georgia,serif;color:var(--gold);letter-spacing:.05em;
  margin:2rem 0 .7rem;display:flex;align-items:center;gap:.7rem}
 h2::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--bronze),transparent)}
 h2 .on{font:400 .8rem 'Averia Serif Libre',Georgia,serif;letter-spacing:0;text-transform:none}
 .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(7rem,1fr));gap:.6rem}
 .stat{background:linear-gradient(180deg,#211a12,var(--panel));border:1px solid var(--line);
  border-radius:3px;padding:.6rem .7rem;text-align:center}
 .stat .v{font:600 1.2rem 'Cinzel',Georgia,serif;color:var(--gold)}
 .stat .k{font-size:.72rem;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
 .facts{color:var(--muted);font-size:.85rem;margin:.6rem 0 0}
 .facts b{color:var(--gold-dim);font-weight:400}
 /* The same chart the dashboard draws, so the two read alike. */
 .chart{background:linear-gradient(180deg,#211a12,var(--panel));border:1px solid var(--line);
  border-radius:3px;padding:.7rem .8rem .35rem;margin:.8rem 0 0}
 .chart figcaption{font:600 .9rem 'Cinzel',Georgia,serif;color:var(--gold-dim);
  letter-spacing:.03em;margin-bottom:.2rem}
 .chart svg{display:block;width:100%;height:auto}
 .chart .bar{fill:#c9822e} .chart .grid{stroke:var(--line);stroke-width:1}
 .chart .base{stroke:var(--bronze);stroke-width:1}
 .chart .ax{fill:var(--muted);font-size:10px;font-family:'Averia Serif Libre',Georgia,serif}
 /* No tooltip layer on this page, so the hit areas only need to stay invisible. */
 .chart .hit{fill:transparent}
 @media (max-width:640px){.chart .ax{font-size:19px}}
</style></head><body>
<h1>You</h1>
<div class="who">__WHO__ &middot; <a href="/">back to the dashboard</a></div>
__STATS__
<h2>Your characters</h2>
__BODY__
__LEAD__
__SESSIONS__
<p class="note">Only characters this Steam account has been seen playing are listed &mdash; Skald
 reads that pairing from the server&rsquo;s own log, so there is nothing to type in, nothing to
 prove, and no way to take a character you have not played. None of it is public: your
 characters are not shown on the dashboard or in the API, and no Steam ID ever is.</p>
<script>
 const f=new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
 for(const el of document.querySelectorAll('time[data-ts]'))
  el.textContent=f.format(new Date(el.dataset.ts*1000));
</script>
</body></html>"""


def render_diagnostics(d):
    rows = []
    for name, info in d["paths"].items():
        rows.append(
            f'<tr><td>{html.escape(name)}</td><td class="mono">{html.escape(info["path"])}</td>'
            f'<td>{yes_no(info["exists"])}</td><td>{yes_no(info["readable"])}</td>'
            f'<td>{yes_no(info["writable"]) if "writable" in info else "&mdash;"}</td></tr>')
    worlds = []
    for w in d["worlds"]:
        ev, sv, bk = w["events_file"], w["saves"], w["backups"]
        game = (yes_no(w["game_version_verified"], w["game_version"], w["game_version"])
                if w["game_version"] else DASH)
        every = (f'every {round(sv["interval"] / 60)}m' if sv.get("interval") else DASH)
        fmt = (yes_no(sv["version_known"], str(sv["version"]), str(sv["version"]))
               if sv["version"] else DASH)
        worlds.append(
            f'<tr><td>{html.escape(w["world"])}</td>'
            f'<td>{yes_no(w["status"] == "up", w["status"], w["status"])}</td>'
            f'<td>{yes_no(ev["exists"])} <span class="muted">{ev["source"]}, '
            f'{ev["sessions_seen"]} session{"" if ev["sessions_seen"] == 1 else "s"}'
            f'</span></td>'
            f'<td>{yes_no(bool(sv["latest"]), sv["latest"] or "none", "none")}</td>'
            f'<td>{yes_no(bk["count"] > 0, str(bk["count"]), "0")}</td>'
            f'<td>{w["milestones"]}</td><td>{w["biomes_unlocked"]}</td>'
            f'<td>{every}</td><td>{game}</td><td>{fmt}</td></tr>')
    srcs = "".join(f'<tr><td>{html.escape(k)}</td><td class="mono">{html.escape(str(v))}</td></tr>'
                   for k, v in sorted(d["config"]["sources"].items()))
    return (DIAG_PAGE
            .replace("__VERSION__", html.escape(d["version"]))
            .replace("__CONFIG_PATH__", html.escape(d["config"]["path"]))
            .replace("__DB_PATH__", html.escape(d["database"]["path"]))
            .replace("__DB_EVENTS__", f'{d["database"]["events"]:,}')
            .replace("__DB_SIZE__", f'{d["database"]["size_bytes"] / 1024:,.0f}')
            .replace("__PATHS__", "\n".join(rows))
            .replace("__WORLDS__", "\n".join(worlds) or
                     '<tr><td colspan="7" class="muted">no worlds configured</td></tr>')
            .replace("__SAVE_VERSIONS__",
                     ", ".join(map(str, d["compatibility"]["save_versions_known"])))
            .replace("__GAME_VERSIONS__",
                     ", ".join(f'{v}.x' for v in d["compatibility"]["game_versions_verified"]))
            .replace("__UNKNOWN_LINES__", f'{d["compatibility"]["unrecognised_lines"]:,}')
            .replace("__SOURCES__", srcs))


DIAG_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Diagnostics &middot; Skald</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Averia+Serif+Libre:wght@400;700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&display=swap" rel="stylesheet">
<style>
 :root{color-scheme:dark;--bg:#0e0b08;--panel:#1c1610;--panel-2:#241c14;--line:#3b2e20;
  --gold:#e8b25a;--gold-dim:#cfa266;--fg:#eadcc0;--muted:#a8977a;--ok:#8fc46a;--bad:#e07a5f}
 body{font:15px/1.5 'Averia Serif Libre',Georgia,serif;margin:0 auto;max-width:56rem;
  padding:1.75rem 1rem 3rem;color:var(--fg);background:var(--bg)}
 h1{font:700 1.6rem 'Cinzel',Georgia,serif;color:var(--gold);letter-spacing:.06em;margin:0 0 .2rem}
 h2{font:600 1rem 'Cinzel',Georgia,serif;color:var(--gold);letter-spacing:.05em;margin:1.8rem 0 .6rem}
 a{color:var(--gold)} .muted{color:var(--muted)} .mono{font-family:ui-monospace,monospace;font-size:.85rem}
 .ok{color:var(--ok)} .bad{color:var(--bad)}
 .wrap{overflow-x:auto}
 table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);border-radius:3px}
 th,td{text-align:left;padding:.4rem .6rem;border-bottom:1px solid var(--line);white-space:nowrap}
 tr:last-child td{border-bottom:0}
 th{background:var(--panel-2);font:600 .72rem 'Cinzel',Georgia,serif;color:var(--gold-dim);
  letter-spacing:.06em;text-transform:uppercase}
</style></head><body>
<h1>Diagnostics</h1>
<p class="muted">Skald __VERSION__ &middot; config: <span class="mono">__CONFIG_PATH__</span>
 &middot; <a href="/">back to the dashboard</a> &middot; <a href="/api/diagnostics">json</a></p>
<p class="muted">Database: <span class="mono">__DB_PATH__</span> &middot;
 __DB_EVENTS__ events &middot; __DB_SIZE__ KB</p>
<h2>Paths</h2>
<div class="wrap"><table><thead><tr><th>What</th><th>Path</th><th>Exists</th><th>Readable</th>
 <th>Writable</th></tr></thead><tbody>
__PATHS__
</tbody></table></div>
<p class="muted">Skald only needs to write to <b>events</b> (to open the directory up for the
 game's log hook, which runs as a different user) and <b>data</b> (its own state). Everything
 else it reads.</p>
<h2>Worlds</h2>
<div class="wrap"><table><thead><tr><th>World</th><th>Status</th><th>Events</th><th>Latest save</th>
 <th>Backups</th><th>Milestones</th><th>Biomes</th><th>Saves</th><th>Game</th><th>Save fmt</th>
 </tr></thead><tbody>
__WORLDS__
</tbody></table></div>
<p class="muted">How often a world saves is how precisely a boss kill can be dated, unless the
 server logs the key being set &mdash; which Valheim 1.0.x does not. Pass
 <span class="mono">-saveinterval &lt;seconds&gt;</span> to the server to tighten it.</p>
<p class="muted">No events means the log hook is not reaching Skald: check the game server's
 hook and that both containers share the events volume. No save means the world's directory is
 not mounted, which is what the clock, the weather and 30-minute kill windows come from.</p>
<h2>Keeping up with Valheim</h2>
<p class="muted">Skald reads files and log lines the game owns, and the game changes. Save
 formats it knows: <b>__SAVE_VERSIONS__</b>. Game versions its weather tables and log patterns
 were checked against: <b>__GAME_VERSIONS__</b>. Lines carrying the game's timestamp that
 matched nothing it knows: <b>__UNKNOWN_LINES__</b> &mdash; a number that climbs after an update
 means a line has been reworded, and something is quietly missing.</p>
<h2>Where each setting came from</h2>
<div class="wrap"><table><thead><tr><th>Setting</th><th>Source</th></tr></thead><tbody>
__SOURCES__
</tbody></table></div>
</body></html>"""


def current_user(headers):
    """The signed-in user behind a request, or None."""
    if not CONFIG.steam_login:
        return None
    token = auth.token_from_cookies(headers.get("Cookie"))
    if not token:
        return None
    return store.session_user(db(), auth.token_hash(token), time.time())


def sign_in_widget(user):
    """The one piece of chrome authentication adds to the page."""
    if not CONFIG.steam_login:
        return ""
    if user:
        name = html.escape(user["display_name"] or f'Steam {user["steam_id"][-4:]}')
        # The character is what a player recognises themselves by here; the
        # Steam persona is only how they signed in.
        who = (f'<b>{html.escape(user["character"])}</b> <span>({name})</span>'
               if user.get("character") else f'<b>{name}</b>')
        return ('<form class="who" method="post" action="/auth/logout">'
                f'<span>signed in as {who}</span>'
                '<a class="signin" href="/me">your characters</a>'
                '<button type="submit">sign out</button></form>')
    return '<a class="who signin" href="/auth/login">sign in through Steam</a>'


class Handler(BaseHTTPRequestHandler):
    # Don't advertise the Python version to the internet.
    server_version = "skald"
    sys_version = ""

    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json(self, obj):
        self._send(200, json.dumps(obj, indent=2), "application/json")

    def _redirect(self, where, cookie=None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self):
        """The posted form, capped: this is an unauthenticated entry point."""
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
        except ValueError:
            return {}
        body = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}

    def do_POST(self):
        # Everything that changes state is a POST -- and SameSite=Lax keeps
        # another site from making your browser do it with your cookie.
        path = self.path.split("?")[0]
        if not CONFIG.steam_login:
            return self._send(404, "not found", "text/plain")
        if path == "/auth/logout":
            token = auth.token_from_cookies(self.headers.get("Cookie"))
            if token:
                store.end_session(db(), auth.token_hash(token))
            return self._redirect("/", auth.clear_cookie_header(CONFIG.secure_cookies))
        if path in ("/me/primary", "/me/hide", "/me/mine"):
            return self._me_post(path)
        self._send(404, "not found", "text/plain")

    def _me_post(self, path):
        user = current_user(self.headers)
        if not user:
            return self._send(403, "sign in first", "text/plain")
        name = self._form().get("name", "")
        conn, steam_id = db(), user["steam_id"]
        # The check that matters, and the only one: the log has to agree this
        # account played that character. Rows exist for nothing else, so a
        # name that was never synced simply misses every statement below.
        if name:
            if path == "/me/primary":
                store.set_primary(conn, steam_id, name)
            else:
                store.hide_character(conn, steam_id, name, path == "/me/hide")
                sync_user(conn, user, history(), time.time())
        return self._redirect("/me")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path.startswith("/auth/"):
            return self._auth(path, query)
        if path == "/healthz":
            # Reaching the database is the point: a Skald that cannot is a
            # Skald whose every page fails, and "healthy" would be a lie.
            try:
                db().execute("SELECT 1")
            except Exception as e:
                return self._send(503, f"database unavailable: {e}", "text/plain")
            return self._send(200, "ok", "text/plain")

        params = urllib.parse.parse_qs(query)

        def arg(name, default):
            v = params.get(name, [""])[0]
            return int(v) if v.isdigit() else default

        now = time.time()
        h = history()
        # The page always shows one world (the default if none is named);
        # the API covers every world unless ?world= narrows it.
        requested = params.get("world", [""])[0]
        world = pick_world(h, requested)
        if world is None:
            return self._send(404, "no such world", "text/plain")
        hw = for_world(h, world) if requested else h
        if path == "/api/online":
            self._json({"worlds": [w for w in online_now(h, now)
                                   if not requested or w["world"] == world]})
        elif path == "/api/playtime":
            self._json({"players": playtime(hw, now)})
        elif path == "/api/sessions":
            self._json({"sessions": recent(hw, now, arg("limit", 100))})
        elif path == "/api/deaths":
            self._json({"deaths": list(reversed(hw["deaths"][-arg("limit", 100):]))})
        elif path == "/api/diagnostics":
            self._json(diagnostics(h, now))
        elif path == "/diagnostics":
            self._send(200, render_diagnostics(diagnostics(h, now)),
                       "text/html; charset=utf-8")
        elif path == "/api/weather":
            self._json({"worlds": [r for w in worlds_of(h)
                                   if (not requested or w == world)
                                   and (r := weather_report(h, w, now))]})
        elif path == "/api/world":
            self._json({"worlds": [
                {"world": w, "modified": (status_of(w) or {}).get("modified"),
                 "game_version": (status_of(w) or {}).get("game_version"),
                 **(world_settings(h, w) or {"preset": None, "modifiers": [],
                                             "since": None})}
                for w in worlds_of(h) if not requested or w == world]})
        elif path == "/api/milestones":
            self._json({"milestones": [m for m in milestones(h)
                                       if not requested or m["world"] == world]})
        elif path == "/api/daily":
            self._json({"days": daily(hw, now, max(1, min(arg("days", CHART_DAYS), 366)))})
        elif path == "/me":
            user = current_user(self.headers)
            if not user:
                return self._redirect("/auth/login" if CONFIG.steam_login else "/")
            mine = sync_user(db(), user, h, now)
            rows = store.characters(db(), user["steam_id"])
            stats = (me_stats(h, now, [c["name"] for c in rows if not c["hidden"]],
                              user["character"]) if user["character"] else None)
            self._send(200, render_me(user, mine, rows, stats),
                       "text/html; charset=utf-8")
        elif path in ("/", "/index.html"):
            user = current_user(self.headers)
            if user:
                # Here as well as on /me, so someone who signs in and never
                # opens that page is still called by their character -- and
                # so a character played since is picked up.
                sync_user(db(), user, h, now)
            self._send(200, render(h, now, world, user),
                       "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def _auth(self, path, query):
        if not CONFIG.steam_login:
            return self._send(404, "sign-in is not configured", "text/plain")
        if path == "/auth/login":
            return self._redirect(auth.login_url(CONFIG.base_url))
        if path == "/auth/callback":
            # Everything in this query string came from the visitor's
            # browser. Steam has to vouch for it before it means anything.
            steam_id = auth.verify(query, CONFIG.base_url)
            if not steam_id:
                return self._send(403, "Steam did not confirm that sign-in.",
                                  "text/plain")
            when = time.time()
            store.put_user(db(), steam_id,
                           auth.fetch_profile(steam_id, CONFIG.steam_api_key), when)
            token = auth.new_token()
            store.start_session(db(), auth.token_hash(token), steam_id, when,
                                when + CONFIG.session_days * 86400)
            store.expire_sessions(db(), when)
            return self._redirect("/", auth.cookie_header(
                token, CONFIG.secure_cookies, CONFIG.session_days))
        return self._send(404, "not found", "text/plain")

    def log_message(self, *a):
        pass  # keep the container logs quiet


def main():
    apply_config(configuration.load())
    # The game's log hook runs as the game container's own user, and a fresh
    # named volume is root-owned 0755, so nothing can write to it. Opening it
    # up needs to be tried, not assumed: Skald itself runs unprivileged, and
    # a volume someone has already set up correctly is not ours to change.
    # The diagnostics page says whether it worked.
    for d in (EVENTS_DIR, DATA_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            print(f"cannot create {d}: {e}", flush=True)
    try:
        os.chmod(EVENTS_DIR, 0o1777)
    except OSError:
        if not os.access(EVENTS_DIR, os.W_OK):
            print(f"note: {EVENTS_DIR} is not writable by this user and could not be "
                  "opened up; the game's log hook may not be able to write events "
                  "there. See /diagnostics.", flush=True)
    try:
        db()
    except Exception as e:
        sys.exit(
            f"skald cannot open its database at {os.path.join(DATA_DIR, 'skald.db')}: {e}\n"
            f"\n{DATA_DIR} must be writable by the user skald runs as "
            f"(uid {os.getuid()}). A fresh Docker named volume inherits the "
            "image's ownership and needs nothing; a bind mount or a volume "
            "from an older version may need:\n"
            f"  chown -R {os.getuid()}:{os.getgid()} <the directory or volume>\n"
            "See the README's Permissions section.")
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=milestone_poller, daemon=True).start()
    print(f"skald {__version__} listening on :{PORT}, worlds={sorted(SERVERS)}"
          f", config={CONFIG.path or 'environment and defaults'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
