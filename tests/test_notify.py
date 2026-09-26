"""Telling people what happened.

The two things worth testing hard: that a signed-in stranger cannot aim the
server at a machine of their choosing, and that nothing announces a backlog
-- a restart must not shout about everything that ever happened.
"""
from tests.conftest import ALFR, BERA, STEAM, WORLD, join, leave

from skald import app, notify, store

T = 1_800_000_000
NOW = T + 3600
HOOK = "https://discord.com/api/webhooks/123/abcdefg"


class Sent:
    """A webhook that records instead of posting."""

    def __init__(self, fail=False):
        self.messages, self.fail = [], fail

    def __call__(self, url, text, **kw):
        if self.fail:
            raise OSError("webhook answered 404")
        self.messages.append((url, text))


def subscriber(kinds=("online", "milestone"), steam_id=STEAM[BERA]):
    store.put_user(app.db(), steam_id, {"display_name": "sub"}, T)
    store.subscribe(app.db(), steam_id, HOOK, list(kinds), T)
    return steam_id


def reset():
    app.WATCH.update(primed=False, online=set(), milestones=set())


# --- what Skald will post to -------------------------------------------

def test_only_discord_webhooks_are_accepted():
    assert notify.check_url(HOOK) == HOOK
    assert notify.check_url("https://canary.discord.com/api/webhooks/1/x")
    for bad in ["http://discord.com/api/webhooks/1/x",      # not https
                "https://evil.example.com/api/webhooks/1/x",  # not Discord
                "https://discord.com/somewhere/else",       # not a webhook
                "https://discord.com.evil.test/api/webhooks/1/x",
                "http://127.0.0.1/admin", "http://[::1]:8080/", "", None]:
        assert notify.check_url(bad) is None, bad


def test_the_url_is_never_shown_in_full():
    shown = notify.masked(HOOK)
    assert "abcdefg" not in shown and "discord.com" in shown


# --- what gets sent ----------------------------------------------------

def test_the_first_tick_announces_nothing(tracker):
    """Otherwise every restart shouts the whole history at everyone."""
    h = tracker.write_events(join(T, ALFR))
    subscriber()
    reset()
    sent = Sent()
    assert app.notify_tick(h, NOW, send=sent) == []
    assert sent.messages == []


def test_someone_arriving_is_announced(tracker):
    tracker.write_events(join(T, ALFR), leave(T + 60, ALFR))
    subscriber()
    reset()
    sent = Sent()
    app.notify_tick(app.history(), NOW, send=sent)        # prime
    h = tracker.write_events(join(T, ALFR), leave(T + 60, ALFR), join(T + 600, BERA))
    app.notify_tick(h, NOW, send=sent)
    assert len(sent.messages) == 1
    assert BERA in sent.messages[0][1] and WORLD in sent.messages[0][1]


def test_nobody_is_told_they_have_arrived(tracker):
    """The subscriber's own character showing up is not news to them."""
    steam = subscriber()
    store.sync_characters(app.db(), steam, [ALFR], T)
    tracker.write_events(join(T, BERA))
    reset()
    sent = Sent()
    app.notify_tick(app.history(), NOW, send=sent)
    h = tracker.write_events(join(T, BERA), join(T + 600, ALFR))
    app.notify_tick(h, NOW, send=sent)
    assert sent.messages == []


def test_only_the_kinds_asked_for(tracker):
    tracker.write_events(join(T, ALFR), leave(T + 60, ALFR))
    subscriber(kinds=["milestone"])
    reset()
    sent = Sent()
    app.notify_tick(app.history(), NOW, send=sent)
    h = tracker.write_events(join(T, ALFR), leave(T + 60, ALFR), join(T + 600, BERA))
    app.notify_tick(h, NOW, send=sent)
    assert sent.messages == []


def test_a_boss_falling_is_announced(tracker):
    tracker.write_save(1, ["defeated_eikthyr"], T)
    app.scan_saves()
    subscriber()
    reset()
    sent = Sent()
    app.notify_tick(app.history(), NOW, send=sent)         # prime
    tracker.write_save(2, ["defeated_eikthyr", "defeated_gdking"], T + 1800)
    app.scan_saves()
    app.notify_tick(app.history(), NOW, send=sent)
    assert len(sent.messages) == 1
    assert "Elder" in sent.messages[0][1]


# --- when the far end is broken ----------------------------------------

def test_a_dead_webhook_is_given_up_on(tracker):
    steam = subscriber()
    reset()
    failing = Sent(fail=True)
    app.notify_tick(app.history(), NOW, send=failing)
    # Each tick must find someone newly online, or there is nothing to fail
    # at: a fresh world per round, with the session left open.
    for i in range(12):
        tracker.write_events(join(T + i * 60, BERA), world=f"World{i}")
        app.notify_tick(app.history(), NOW, send=failing)
    row = store.subscription(app.db(), steam)
    assert row["enabled"] == 0, "a webhook that always 404s must be given up on"
    assert "404" in (row["last_error"] or "")


def test_a_failure_never_stops_the_tick(tracker):
    subscriber()
    tracker.write_events(join(T, ALFR), leave(T + 60, ALFR))
    reset()
    failing = Sent(fail=True)
    app.notify_tick(app.history(), NOW, send=failing)
    h = tracker.write_events(join(T, ALFR), leave(T + 60, ALFR), join(T + 600, BERA))
    app.notify_tick(h, NOW, send=failing)          # must not raise


def test_a_good_send_clears_an_earlier_failure(tracker):
    steam = subscriber()
    store.delivery_failed(app.db(), steam, "boom")
    assert store.subscription(app.db(), steam)["failures"] == 1
    store.delivered(app.db(), steam, NOW)
    row = store.subscription(app.db(), steam)
    assert row["failures"] == 0 and row["last_error"] is None
