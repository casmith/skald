"""Settings: a TOML file, the environment over it, defaults under both."""
from skald import config

TOML = """
port = 9999
timezone = "Europe/Stockholm"
default_world = "Testheim"
events_dir = "/srv/events"

[[worlds]]
name = "Testheim"
status_url = "http://testheim/status.json"

[[worlds]]
name = "Utgard"
status_url = "http://utgard/status.json"
saves_dir = "/elsewhere/utgard"
"""


def write(tmp_path, text=TOML):
    p = tmp_path / "skald.toml"
    p.write_text(text)
    return str(p)


def test_defaults_alone_are_enough(tmp_path):
    cfg = config.load(str(tmp_path / "missing.toml"), env={})
    assert cfg.port == 8080 and cfg.events_dir == "/events" and cfg.worlds == ()
    assert cfg.sources["port"] == "default"


def test_the_file_is_read(tmp_path):
    cfg = config.load(write(tmp_path), env={})
    assert cfg.port == 9999
    assert cfg.timezone == "Europe/Stockholm"
    assert [w.name for w in cfg.worlds] == ["Testheim", "Utgard"]
    assert cfg.sources["port"] == "file"


def test_the_environment_wins(tmp_path):
    cfg = config.load(write(tmp_path), env={"SKALD_PORT": "1234"})
    assert cfg.port == 1234
    assert cfg.sources["port"] == "$SKALD_PORT"
    assert cfg.timezone == "Europe/Stockholm"  # untouched by the environment


def test_the_old_variable_names_still_work(tmp_path):
    """The deploy this came from sets TRACKER_*; breaking it buys nothing."""
    env = {"TRACKER_PORT": "7000", "TRACKER_TZ": "America/Chicago",
           "TRACKER_SERVERS": "Alpha=http://a/status.json,Beta=http://b/status.json",
           "TRACKER_DEFAULT_WORLD": "Beta"}
    cfg = config.load(str(tmp_path / "missing.toml"), env=env)
    assert cfg.port == 7000 and cfg.timezone == "America/Chicago"
    assert [w.name for w in cfg.worlds] == ["Alpha", "Beta"]
    assert cfg.default_world == "Beta"
    assert cfg.sources["worlds"] == "$TRACKER_SERVERS"


def test_worlds_from_the_environment_replace_the_file(tmp_path):
    cfg = config.load(write(tmp_path), env={"SKALD_WORLDS": "Only=http://only/x.json"})
    assert [w.name for w in cfg.worlds] == ["Only"]


def test_a_default_world_that_does_not_exist_is_dropped(tmp_path):
    cfg = config.load(write(tmp_path), env={"SKALD_DEFAULT_WORLD": "Nowhere"})
    assert cfg.default_world == ""
    assert "no such world" in cfg.sources["default_world"]


def test_paths_follow_the_game_layout(tmp_path):
    cfg = config.load(write(tmp_path), env={})
    assert cfg.saves_dir("Testheim").endswith("/saves/Testheim/worlds_local/Testheim")
    assert cfg.backups_dir("Testheim").endswith("/nas/testheim/backups")


def test_a_world_can_override_where_it_lives(tmp_path):
    cfg = config.load(write(tmp_path), env={})
    assert cfg.saves_dir("Utgard") == "/elsewhere/utgard"


def test_the_config_path_can_come_from_the_environment(tmp_path):
    path = write(tmp_path)
    cfg = config.load(env={"SKALD_CONFIG": path})
    assert cfg.path == path and cfg.port == 9999
