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
    # v4: who is signed in. A session keeps only the hash of its token, so
    # a stolen copy of this database cannot be used to sign in as anyone.
    """
    CREATE TABLE users (
        steam_id     TEXT PRIMARY KEY,
        display_name TEXT,
        avatar       TEXT,
        first_seen   REAL NOT NULL,
        last_seen    REAL NOT NULL
    );

    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        steam_id   TEXT NOT NULL REFERENCES users(steam_id),
        created    REAL NOT NULL,
        expires    REAL NOT NULL
    );
    CREATE INDEX sessions_by_expiry ON sessions (expires);
    """,
    # v5: which characters a player owns, and which one is them.
    #
    # Nothing here is taken on trust. The log pairs a connection's SteamID
    # with the character that follows it, so Skald already knows who played
    # what; these rows only record what to *do* with that -- which is why
    # they fill themselves in and a player who never opens the page still
    # gets the right name.
    #
    # `chosen` is the whole reason a primary is stored rather than computed:
    # the default is the most-played character and follows the playtime, but
    # once someone has picked one by hand it must stop moving under them.
    # `hidden` is the same idea for a character they say is not theirs -- a
    # tombstone, because the log will keep offering it otherwise.
    """
    CREATE TABLE characters (
        steam_id   TEXT NOT NULL REFERENCES users(steam_id),
        name       TEXT NOT NULL,
        first_seen REAL NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 0,
        chosen     INTEGER NOT NULL DEFAULT 0,
        hidden     INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (steam_id, name)
    );
    CREATE UNIQUE INDEX one_primary_per_player
        ON characters (steam_id) WHERE is_primary = 1;
    """,
    # v6: where to send someone a message, and what about. One row per
    # player: a webhook is a place, not a per-event setting, and `kinds`
    # holds what they asked for.
    #
    # `failures` exists because a webhook someone deleted in Discord answers
    # 404 for ever, and a dashboard that keeps posting into the void every
    # minute is the sort of thing that gets an IP rate-limited.
    """
    CREATE TABLE subscriptions (
        steam_id   TEXT PRIMARY KEY REFERENCES users(steam_id),
        url        TEXT NOT NULL,
        kinds      TEXT NOT NULL,          -- JSON list
        created    REAL NOT NULL,
        enabled    INTEGER NOT NULL DEFAULT 1,
        failures   INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        last_sent  REAL
    );
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
    if not os.path.isfile(path):
        return 0  # a directory where a log should be: see diagnostics
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


def clear_skipped(conn, paths):
    """Forget the unrecognised-line count for these files.

    Only called for files that are now read as whole server logs, where the
    count was never meaningful. Cheap and idempotent: the WHERE clause makes
    it a no-op once it has run.
    """
    if not paths:
        return
    marks = ",".join("?" * len(paths))
    with conn:
        conn.execute(f"UPDATE files SET skipped = 0 WHERE skipped > 0"
                     f" AND path IN ({marks})", list(paths))


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


def put_user(conn, steam_id, profile, when):
    with conn:
        conn.execute(
            "INSERT INTO users (steam_id, display_name, avatar, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(steam_id) DO UPDATE SET last_seen = ?,"
            " display_name = coalesce(nullif(?, ''), display_name),"
            " avatar = coalesce(nullif(?, ''), avatar)",
            (steam_id, profile.get("display_name", ""), profile.get("avatar", ""),
             when, when, when, profile.get("display_name", ""), profile.get("avatar", "")))


def start_session(conn, token_hash, steam_id, created, expires):
    with conn:
        conn.execute("INSERT OR REPLACE INTO sessions (token_hash, steam_id, created,"
                     " expires) VALUES (?, ?, ?, ?)",
                     (token_hash, steam_id, created, expires))


def session_user(conn, token_hash, when):
    """The signed-in user behind a session token, if it is still good."""
    row = conn.execute(
        "SELECT u.steam_id, u.display_name, u.avatar,"
        " (SELECT name FROM characters c WHERE c.steam_id = u.steam_id"
        "  AND c.is_primary = 1) AS character"
        " FROM sessions s JOIN users u ON u.steam_id = s.steam_id"
        " WHERE s.token_hash = ? AND s.expires > ?", (token_hash, when)).fetchone()
    return dict(row) if row else None


def characters(conn, steam_id):
    """This player's characters, primary first, then first seen."""
    return [dict(r) for r in conn.execute(
        "SELECT name, first_seen, is_primary, chosen, hidden FROM characters"
        " WHERE steam_id = ? ORDER BY is_primary DESC, first_seen", (steam_id,))]


def sync_characters(conn, steam_id, ordered, when):
    """Take up the characters the log says are this player's, and pick one.

    `ordered` is every character the account has been seen playing, most
    played first, so the default primary is the one they actually play. It
    is re-applied on every visit and follows the playtime -- until they
    choose one by hand, after which it is left alone. A character they have
    hidden stays hidden however much it is played.
    """
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO characters (steam_id, name, first_seen)"
            " VALUES (?, ?, ?)", [(steam_id, n, when) for n in ordered])
        rows = {r["name"]: r for r in conn.execute(
            "SELECT name, is_primary, chosen, hidden FROM characters"
            " WHERE steam_id = ?", (steam_id,))}
        current = next((n for n, r in rows.items() if r["is_primary"]), None)
        if current and rows[current]["chosen"] and not rows[current]["hidden"]:
            return current
        want = next((n for n in ordered if not rows[n]["hidden"]), None)
        if want is None:  # everything visible is hidden: keep any claim we have
            want = next((n for n, r in rows.items() if not r["hidden"]), None)
        if want != current:
            _set_primary(conn, steam_id, want)
        return want


def set_primary(conn, steam_id, name):
    """Pick a primary by hand, and stop it moving. Unknown names are ignored."""
    with conn:
        row = conn.execute("SELECT hidden FROM characters WHERE steam_id = ? AND name = ?",
                           (steam_id, name)).fetchone()
        if row is None or row["hidden"]:
            return False
        _set_primary(conn, steam_id, name)
        conn.execute("UPDATE characters SET chosen = 1 WHERE steam_id = ? AND name = ?",
                     (steam_id, name))
        return True


def hide_character(conn, steam_id, name, hidden=True):
    """Say a character is not yours, or take that back.

    Hiding one also drops it as primary and forgets that it was chosen, so
    the next sync picks a fresh default rather than leaving the player with
    a primary they have just disowned.
    """
    with conn:
        conn.execute("UPDATE characters SET hidden = ? WHERE steam_id = ? AND name = ?",
                     (1 if hidden else 0, steam_id, name))
        if hidden:
            conn.execute("UPDATE characters SET is_primary = 0, chosen = 0"
                         " WHERE steam_id = ? AND name = ?", (steam_id, name))


def _set_primary(conn, steam_id, name):
    """Caller holds the transaction: the unique index rejects two primaries,
    so clearing the old one has to land in the same statement sequence."""
    conn.execute("UPDATE characters SET is_primary = 0, chosen = 0 WHERE steam_id = ?",
                 (steam_id,))
    if name is not None:
        conn.execute("UPDATE characters SET is_primary = 1"
                     " WHERE steam_id = ? AND name = ?", (steam_id, name))


def end_session(conn, token_hash):
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def expire_sessions(conn, when):
    with conn:
        conn.execute("DELETE FROM sessions WHERE expires <= ?", (when,))


def subscription(conn, steam_id):
    row = conn.execute("SELECT * FROM subscriptions WHERE steam_id = ?",
                       (steam_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["kinds"] = json.loads(out["kinds"])
    return out


def subscribers(conn, kind):
    """Everyone who wants this kind of message, and can still be sent one."""
    out = []
    for row in conn.execute("SELECT * FROM subscriptions WHERE enabled = 1"):
        d = dict(row)
        d["kinds"] = json.loads(d["kinds"])
        if kind in d["kinds"]:
            out.append(d)
    return out


def subscribe(conn, steam_id, url, kinds, when):
    """Save where to send, and what. Re-enables a webhook that had failed."""
    with conn:
        conn.execute(
            "INSERT INTO subscriptions (steam_id, url, kinds, created)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(steam_id) DO UPDATE SET url = ?, kinds = ?,"
            " enabled = 1, failures = 0, last_error = NULL",
            (steam_id, url, json.dumps(kinds), when, url, json.dumps(kinds)))


def unsubscribe(conn, steam_id):
    with conn:
        conn.execute("DELETE FROM subscriptions WHERE steam_id = ?", (steam_id,))


def delivered(conn, steam_id, when):
    with conn:
        conn.execute("UPDATE subscriptions SET failures = 0, last_error = NULL,"
                     " last_sent = ? WHERE steam_id = ?", (when, steam_id))


def delivery_failed(conn, steam_id, error, give_up_at=10):
    """Count a failure, and stop trying once a webhook is clearly gone."""
    with conn:
        conn.execute(
            "UPDATE subscriptions SET failures = failures + 1, last_error = ?,"
            " enabled = CASE WHEN failures + 1 >= ? THEN 0 ELSE enabled END"
            " WHERE steam_id = ?", (str(error)[:200], give_up_at, steam_id))


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
