"""Skald's database: one SQLite file under `data_dir`.

The hook's event files stay the source of truth for *new* lines -- they are
what the game server writes -- but they are not a good long-term home. They
can be rotated, trimmed or lost, they get re-read in full on every change,
and there is nowhere to put anything Skald learns by itself. So every line is
ingested once, keyed by its own text, and everything downstream reads rows.

Ingestion is incremental: each file's size and modification time are
remembered, and a file that has only grown is read from where it left off.
A file that shrank (rotated, or replaced by a restored backup) is read again
from the start, where the primary key makes the repeats free.

What was in milestones.json moves here too, and is imported on first run.
"""
import json
import os
import sqlite3

SCHEMA = [
    # v1: events, milestone state, the latest save seen per world
    """
    CREATE TABLE events (
        world TEXT NOT NULL,
        line  TEXT NOT NULL,          -- the log line, from its timestamp on
        ts    REAL NOT NULL,
        prio  INTEGER NOT NULL,       -- ordering within the same second
        kind  TEXT NOT NULL,
        args  TEXT NOT NULL,          -- JSON list, as the parser produced it
        PRIMARY KEY (world, line)
    );
    CREATE INDEX events_by_time ON events (world, ts, prio);

    CREATE TABLE files (              -- how far each event file has been read
        path  TEXT PRIMARY KEY,
        size  INTEGER NOT NULL,
        mtime REAL NOT NULL
    );

    CREATE TABLE milestones (         -- when a world's keys were first seen
        world  TEXT NOT NULL,
        key    TEXT NOT NULL,
        after  REAL,                  -- NULL: already set when first seen
        by     REAL NOT NULL,
        source TEXT NOT NULL,         -- 'save' or 'backup'
        PRIMARY KEY (world, key)
    );

    CREATE TABLE saves (              -- the newest save and backup seen
        world      TEXT PRIMARY KEY,
        name       TEXT,
        ts         REAL,
        world_time REAL,
        keys       TEXT,              -- JSON list
        backup     TEXT,              -- newest backup filename scanned
        backup_ts  REAL
    );

    CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """,
    # v2: what Skald met but did not understand -- the save format's version
    # number, and lines that look like the game's but match no known pattern.
    # Valheim updates change both, and silence is the wrong way to find out.
    """
    ALTER TABLE saves ADD COLUMN save_version INTEGER;
    ALTER TABLE files ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0;
    """,
    # v3: when the save before the current one landed. The gap between them
    # is how often the world is saved, which is the precision every boss
    # kill is dated to, so it is worth showing.
    """
    ALTER TABLE saves ADD COLUMN prev_ts REAL;
    """,
]


def connect(data_dir):
    """Open (and migrate) the database under `data_dir`."""
    os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(data_dir, "skald.db"),
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    return conn


def migrate(conn):
    """Apply whatever schema steps this database has not had yet."""
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, step in enumerate(SCHEMA[have:], start=have + 1):
        with conn:
            conn.executescript(step)
            conn.execute(f"PRAGMA user_version = {version}")
    return conn.execute("PRAGMA user_version").fetchone()[0]


def ingest(conn, path, world, parse_line, is_log_line=None):
    """Read a file's new lines into `events`. Returns how many were added.

    `parse_line` returns (ts, prio, kind, args, line) for a line worth
    keeping, or None. `is_log_line` says whether a line the parser rejected
    still looked like the game talking: those are counted, because a Valheim
    update that rewords a line would otherwise just go quiet.
    """
    try:
        st = os.stat(path)
    except OSError:
        return 0
    row = conn.execute("SELECT size, mtime FROM files WHERE path = ?", (path,)).fetchone()
    start = 0
    if row and st.st_size >= row["size"] and st.st_mtime >= row["mtime"]:
        if st.st_size == row["size"] and st.st_mtime == row["mtime"]:
            return 0  # untouched since last time
        start = row["size"]  # grown: read only the new tail
    try:
        with open(path, errors="replace") as f:
            f.seek(start)
            # A partial last line (the hook writing as we read) would be
            # parsed wrong, so stop at the last newline and leave the rest
            # for next time by remembering only what we consumed.
            text = f.read()
    except OSError:
        return 0
    consumed = text.rfind("\n") + 1
    rows, skipped = [], 0
    for line in text[:consumed].splitlines():
        ev = parse_line(line)
        if ev:
            ts, prio, kind, args, clean = ev
            rows.append((world, clean, ts, prio, kind, json.dumps(list(args))))
        elif is_log_line and is_log_line(line):
            skipped += 1
    with conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO events (world, line, ts, prio, kind, args)"
            " VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.execute("INSERT INTO files (path, size, mtime, skipped) VALUES (?, ?, ?, ?)"
                     " ON CONFLICT(path) DO UPDATE SET size = ?, mtime = ?,"
                     " skipped = skipped + ?",
                     (path, start + consumed, st.st_mtime, skipped,
                      start + consumed, st.st_mtime, skipped))
        return conn.total_changes - before - 1


def events(conn):
    """Every event, per world, in the order the replay wants them."""
    out = {}
    for row in conn.execute("SELECT world, ts, prio, kind, args FROM events"
                            " ORDER BY world, ts, prio"):
        out.setdefault(row["world"], []).append(
            (row["ts"], row["prio"], row["kind"], tuple(json.loads(row["args"]))))
    return out


def add_event(conn, world, line, ts, prio, kind, args):
    """Record an event Skald worked out for itself (a crash marker)."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO events (world, line, ts, prio, kind, args)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (world, line, ts, prio, kind, json.dumps(list(args))))


def skipped_lines(conn):
    """Lines that looked like the game's but matched nothing, per file."""
    return {row["path"]: row["skipped"] for row in
            conn.execute("SELECT path, skipped FROM files WHERE skipped > 0")}


def state(conn):
    """Milestone and save state, in the shape the rest of the code expects."""
    out = {}
    for row in conn.execute("SELECT * FROM saves"):
        out[row["world"]] = {
            "save": {"last": row["name"], "last_ts": row["ts"],
                     "world_time": row["world_time"],
                     "version": row["save_version"],
                     "prev_ts": row["prev_ts"],
                     "keys": json.loads(row["keys"]) if row["keys"] else None},
            "last": row["backup"] or "", "last_ts": row["backup_ts"],
            "live": {}, "milestones": {},
        }
    for row in conn.execute("SELECT * FROM milestones"):
        world = out.setdefault(row["world"], {
            "save": {"last": "", "last_ts": None, "keys": None},
            "last": "", "last_ts": None, "live": {}, "milestones": {}})
        where = "live" if row["source"] == "save" else "milestones"
        world[where][row["key"]] = {"after": row["after"], "by": row["by"]}
    return out


def put_milestone(conn, world, key, after, by, source):
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO milestones (world, key, after, by, source)"
            " VALUES (?, ?, ?, ?, ?)", (world, key, after, by, source))


def put_save(conn, world, name, ts, world_time, keys, save_version=None, prev_ts=None):
    with conn:
        conn.execute(
            "INSERT INTO saves (world, name, ts, world_time, keys, save_version, prev_ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(world) DO UPDATE SET name = ?, ts = ?, world_time = ?,"
            " keys = ?, save_version = ?, prev_ts = ?",
            (world, name, ts, world_time, json.dumps(keys), save_version, prev_ts,
             name, ts, world_time, json.dumps(keys), save_version, prev_ts))


def put_backup(conn, world, name, ts):
    with conn:
        conn.execute(
            "INSERT INTO saves (world, backup, backup_ts) VALUES (?, ?, ?)"
            " ON CONFLICT(world) DO UPDATE SET backup = ?, backup_ts = ?",
            (world, name, ts, name, ts))


def import_legacy(conn, data_dir):
    """Bring milestones.json in, once, so upgrades keep their history.

    Skald kept its state in a JSON file before this. Deployments have months
    of dated boss kills in one, and the backups that dated them may well be
    pruned by now -- losing that would be losing the only copy.
    """
    if conn.execute("SELECT v FROM meta WHERE k = 'imported_json'").fetchone():
        return 0
    path = os.path.join(data_dir, "milestones.json")
    try:
        with open(path) as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    count = 0
    for world, st in old.items():
        save = st.get("save") or {}
        if save.get("last"):
            put_save(conn, world, save["last"], save.get("last_ts"),
                     save.get("world_time"), save.get("keys") or [])
        if st.get("last"):
            put_backup(conn, world, st["last"], st.get("last_ts"))
        for where, source in (("live", "save"), ("milestones", "backup")):
            for key, w in (st.get(where) or {}).items():
                put_milestone(conn, world, key, w.get("after"), w["by"], source)
                count += 1
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('imported_json', ?)",
                     (str(count),))
    return count
