"""Where Skald's settings come from.

A TOML file, with environment variables overriding it, and defaults under
both. TOML because `tomllib` is in the standard library -- Skald has no
dependencies and it would be a shame to add one for a config file.

The older `TRACKER_*` variable names still work: this started life as one
service in a homelab's deploy repo, and breaking those would break the
deployment it came from for no good reason.
"""
import os
import tomllib
from dataclasses import dataclass, field, replace

__all__ = ["Config", "World", "load", "replace"]

DEFAULT_PATH = "/config/skald.toml"

DEFAULTS = {
    "port": 8080,
    "timezone": "UTC",
    "default_world": "",
    "events_dir": "/events",
    "data_dir": "/data",
    "saves_root": "/saves",
    "backups_root": "/nas",
    "poll_seconds": 60,
    "save_scan_seconds": 60,
    "chart_days": 30,
    "merge_gap_seconds": 120,
}

# setting -> (canonical variable, older aliases that still work)
ENV = {
    "port": ("SKALD_PORT", "TRACKER_PORT"),
    "timezone": ("SKALD_TZ", "TRACKER_TZ"),
    "default_world": ("SKALD_DEFAULT_WORLD", "TRACKER_DEFAULT_WORLD"),
    "events_dir": ("SKALD_EVENTS_DIR", "EVENTS_DIR"),
    "data_dir": ("SKALD_DATA_DIR", "DATA_DIR"),
    "saves_root": ("SKALD_SAVES_ROOT", "SAVES_ROOT"),
    "backups_root": ("SKALD_BACKUPS_ROOT", "BACKUPS_ROOT"),
    "poll_seconds": ("SKALD_POLL_SECONDS", "POLL_SECONDS"),
    "save_scan_seconds": ("SKALD_SAVE_SCAN_SECONDS",),
    "chart_days": ("SKALD_CHART_DAYS",),
    "merge_gap_seconds": ("SKALD_MERGE_GAP_SECONDS", "MERGE_GAP_SECONDS"),
}
WORLDS_ENV = ("SKALD_WORLDS", "TRACKER_SERVERS")
# "World=/path/to/log,Other=/path" for people who would rather not write the
# file. Names must match the worlds above.
LOGS_ENV = ("SKALD_LOG_FILES",)
INTS = {"port", "poll_seconds", "save_scan_seconds", "chart_days", "merge_gap_seconds"}


@dataclass(frozen=True)
class World:
    name: str
    status_url: str = ""
    # Where this world's saves and backups are, when they are not under the
    # usual roots. Empty means "work it out from the roots and the name".
    saves_dir: str = ""
    backups_dir: str = ""
    # The server's own log file, for servers that write one rather than
    # running the log hook: a vanilla or systemd install, or a container's
    # json log. Skald reads it the same way, ignoring everything that is not
    # an event. Empty means this world arrives through the hook.
    log_file: str = ""


@dataclass(frozen=True)
class Config:
    port: int = DEFAULTS["port"]
    timezone: str = DEFAULTS["timezone"]
    default_world: str = DEFAULTS["default_world"]
    events_dir: str = DEFAULTS["events_dir"]
    data_dir: str = DEFAULTS["data_dir"]
    saves_root: str = DEFAULTS["saves_root"]
    backups_root: str = DEFAULTS["backups_root"]
    poll_seconds: int = DEFAULTS["poll_seconds"]
    save_scan_seconds: int = DEFAULTS["save_scan_seconds"]
    chart_days: int = DEFAULTS["chart_days"]
    merge_gap_seconds: int = DEFAULTS["merge_gap_seconds"]
    worlds: tuple[World, ...] = ()
    # Where the settings came from, for the diagnostics page to show.
    path: str = ""
    sources: dict[str, str] = field(default_factory=dict)

    def log_files(self):
        """world -> server log path, for the worlds configured that way."""
        return {w.name: w.log_file for w in self.worlds if w.log_file}

    def world(self, name):
        for w in self.worlds:
            if w.name == name:
                return w
        return World(name=name)

    def saves_dir(self, world):
        w = self.world(world)
        if w.saves_dir:
            return w.saves_dir
        # The game's own layout: <root>/<World>/worlds_local/<World>/
        return os.path.join(self.saves_root, world, "worlds_local", world)

    def backups_dir(self, world):
        w = self.world(world)
        if w.backups_dir:
            return w.backups_dir
        return os.path.join(self.backups_root, world.lower(), "backups")


def parse_worlds_env(raw):
    """`Name=url,Other=url` -- the form the deploy repo has always used."""
    out = []
    for item in raw.split(","):
        if "=" in item:
            name, url = item.split("=", 1)
            if name.strip():
                out.append(World(name=name.strip(), status_url=url.strip()))
    return tuple(out)


def read_file(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def load(path=None, env=None):
    """Settings from the file at `path`, overridden by the environment."""
    env = os.environ if env is None else env
    path = path or env.get("SKALD_CONFIG") or DEFAULT_PATH
    sources, data = {}, {}
    used_path = ""
    try:
        data = read_file(path)
        used_path = path
    except FileNotFoundError:
        pass  # a config file is optional; the environment alone is enough

    values = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in data:
            values[key], sources[key] = data[key], "file"
        for var in ENV[key]:
            if env.get(var):
                values[key], sources[key] = env[var], f"${var}"
                break
        sources.setdefault(key, "default")
        if key in INTS:
            values[key] = int(values[key])

    worlds = tuple(World(**w) for w in data.get("worlds", []))
    if worlds:
        sources["worlds"] = "file"
    for var in WORLDS_ENV:
        if env.get(var):
            worlds, sources["worlds"] = parse_worlds_env(env[var]), f"${var}"
            break
    sources.setdefault("worlds", "none")

    for var in LOGS_ENV:
        if env.get(var):
            paths = dict(item.split("=", 1) for item in env[var].split(",") if "=" in item)
            worlds = tuple(replace(w, log_file=paths.get(w.name, w.log_file)) for w in worlds)
            sources["log_files"] = f"${var}"
            break

    cfg = Config(**values, worlds=worlds, path=used_path, sources=sources)
    # A default world that names nothing is worse than no default at all.
    if cfg.default_world and not any(w.name == cfg.default_world for w in worlds):
        cfg = replace(cfg, default_world="")
        cfg.sources["default_world"] = "ignored: no such world"
    return cfg
