"""How a world is set up: its difficulty and modifiers.

Valheim logs these once, at startup, in words -- and advertises them in its
Steam tags as undocumented numeric ids. Skald reads the words and uses the
tag only as a yes/no, which is what most of these tests are about: the two
sources disagreeing, or one of them being absent.
"""
from tests.conftest import ALFR, WORLD, join, line

from skald import app

T = 1_800_000_000
NOW = T + 3600


def modifiers(ts, *pairs):
    return [line(ts, f"Setting world modifier: {k}->{v}") for k, v in pairs]


def preset(ts, name):
    return [line(ts, f"Setting world modifier preset: {name}")]


def test_the_lines_a_real_server_writes(tracker):
    """Copied from a live server, not invented: the shapes must not drift.

    The order is the server's own, which is arbitrary -- Skald sorts them
    into one that reads the same every time.
    """
    h = tracker.write_events(
        modifiers(T, ("portals", "casual"), ("combat", "veryhard"),
                  ("resources", "muchmore"), ("raids", "none"),
                  ("deathpenalty", "casual")))
    st = app.world_settings(h, WORLD)
    assert [(m["setting"], m["value"]) for m in st["modifiers"]] == [
        ("combat", "veryhard"), ("deathpenalty", "casual"),
        ("resources", "muchmore"), ("raids", "none"), ("portals", "casual")]
    assert st["preset"] is None


def test_a_preset_is_not_expanded(tracker):
    """The server logs the preset's name and nothing else, so that is all
    there is to show."""
    h = tracker.write_events(preset(T, "hard"))
    st = app.world_settings(h, WORLD)
    assert st["preset"] == "hard"
    assert st["modifiers"] == []


def test_the_newest_start_wins(tracker):
    """Modifiers are logged once per run. An older run's settings are
    history, not the truth about the world now."""
    h = tracker.write_events(
        preset(T, "hardcore"),
        modifiers(T + 86400, ("combat", "easy")))
    st = app.world_settings(h, WORLD)
    assert st["preset"] is None and st["since"] == T + 86400
    assert [(m["setting"], m["value"]) for m in st["modifiers"]] == [("combat", "easy")]


def test_one_start_holds_together(tracker):
    """The lines land in the same second, but must survive a slow one."""
    h = tracker.write_events(modifiers(T, ("combat", "hard")),
                             modifiers(T + 3, ("raids", "more")))
    assert len(app.world_settings(h, WORLD)["modifiers"]) == 2


def test_a_world_nobody_has_configured(tracker):
    h = tracker.write_events(join(T, ALFR))
    assert app.world_settings(h, WORLD) is None


def test_labels_in_words(tracker):
    h = tracker.write_events(modifiers(T, ("deathpenalty", "casual"),
                                       ("resources", "muchmore")))
    st = app.world_settings(h, WORLD)
    assert [(m["setting_label"], m["value_label"]) for m in st["modifiers"]] == [
        ("Death penalty", "casual"), ("Resources", "much more")]


def test_a_modifier_skald_has_never_heard_of_still_shows(tracker):
    """Valheim can add one without telling us. A blank would be worse than
    an unstyled label."""
    h = tracker.write_events(modifiers(T, ("swimspeed", "veryfast")))
    m = app.world_settings(h, WORLD)["modifiers"][0]
    assert (m["setting_label"], m["value_label"]) == ("Swimspeed", "veryfast")


def test_the_steam_tag_is_read_as_a_yes_or_no():
    """The ids in it are undocumented and renumberable, so only its
    emptiness is trusted."""
    def modified(keywords):
        found = app.MODIFIER_TAG_RE.search(keywords)
        return bool(found and found.group(1).strip())
    assert modified("g=1.0.15,n=40,m=") is False
    assert modified("g=1.0.15,n=40") is False
    assert modified(r"g=1.0.15,n=40,m=0\=70\,1\=200\,19") is True


def test_the_page_names_the_settings(tracker):
    h = tracker.write_events(modifiers(T, ("combat", "veryhard"),
                                       ("raids", "none")))
    page = app.render_world_settings(app.world_settings(h, WORLD), {"modified": True})
    assert "Combat" in page and "very hard" in page
    assert "Raids" in page and "none" in page


def test_a_modified_world_we_never_saw_start_says_so(tracker):
    """The honest case: the server says it is modified, and we were not
    watching when it said which."""
    h = tracker.write_events(join(T, ALFR))
    page = app.render_world_settings(app.world_settings(h, WORLD), {"modified": True})
    assert "Modified" in page and "the next time this server starts" in page


def test_a_default_world_says_default(tracker):
    h = tracker.write_events(join(T, ALFR))
    page = app.render_world_settings(app.world_settings(h, WORLD), {"modified": False})
    assert "Default settings" in page


def test_a_world_we_have_not_heard_from_claims_nothing(tracker):
    h = tracker.write_events(join(T, ALFR))
    assert app.render_world_settings(app.world_settings(h, WORLD), {"modified": None}) == ""
    assert app.render_world_settings(app.world_settings(h, WORLD), None) == ""
