"""Sign in through Steam.

Everything in the callback comes from the visitor's browser, so the tests
that matter are the ones where it lies. Steam is an OpenID 2.0 provider: the
only thing that makes a claimed identity real is Steam confirming it.
"""
import urllib.parse

import pytest

from tests.conftest import WORLD

from skald import app, auth, config, store

BASE = "https://skald.example.com"
STEAM_ID = "76561190000000001"
GOOD = {
    "openid.ns": auth.NS,
    "openid.mode": "id_res",
    "openid.claimed_id": f"https://steamcommunity.com/openid/id/{STEAM_ID}",
    "openid.identity": f"https://steamcommunity.com/openid/id/{STEAM_ID}",
    "openid.return_to": f"{BASE}/auth/callback",
    "openid.sig": "not checked by us -- Steam checks it",
}


class Answer:
    """Steam's reply to the confirmation POST."""

    def __init__(self, body=b"ns:http://specs.openid.net/auth/2.0\nis_valid:true\n"):
        self.body = body
        self.asked = []

    def __call__(self, request, timeout=None):
        self.asked.append(request)
        return self

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def query(**overrides):
    params = dict(GOOD, **overrides)
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def test_the_login_url_points_at_steam_and_back_at_us():
    url = auth.login_url(BASE + "/")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith(auth.STEAM_OPENID)
    assert params["openid.return_to"] == [f"{BASE}/auth/callback"]
    assert params["openid.realm"] == [BASE]
    assert params["openid.mode"] == ["checkid_setup"]


def test_a_confirmed_sign_in_gives_the_steam_id():
    steam = Answer()
    assert auth.verify(query(), BASE, opener=steam) == STEAM_ID
    # We asked Steam to check it, with the mode swapped and nothing else.
    (sent,) = steam.asked
    body = urllib.parse.parse_qs(sent.data.decode())
    assert body["openid.mode"] == ["check_authentication"]
    assert body["openid.sig"] == [GOOD["openid.sig"]]


def test_steam_saying_no_is_a_no():
    steam = Answer(b"ns:http://specs.openid.net/auth/2.0\nis_valid:false\n")
    assert auth.verify(query(), BASE, opener=steam) is None


def test_a_forged_callback_is_refused_even_though_it_looks_right():
    """The whole point: without Steam's confirmation, nothing is believed."""
    steam = Answer(b"is_valid:false\n")
    forged = query(**{"openid.claimed_id":
                      "https://steamcommunity.com/openid/id/76561190000009999"})
    assert auth.verify(forged, BASE, opener=steam) is None


def test_an_assertion_made_out_to_another_site_is_refused():
    """A valid Steam response for somewhere else is not ours to accept."""
    steam = Answer()
    elsewhere = query(**{"openid.return_to": "https://evil.example.com/auth/callback"})
    assert auth.verify(elsewhere, BASE, opener=steam) is None
    assert steam.asked == []  # refused before even asking Steam


@pytest.mark.parametrize("claimed", [
    "https://steamcommunity.com/openid/id/12",             # too short
    "https://evil.example.com/openid/id/76561190000000001",  # not Steam
    "http://steamcommunity.com/openid/id/76561190000000001",  # not https
    "",
])
def test_identities_that_are_not_steam_ids_are_refused(claimed):
    steam = Answer()
    assert auth.verify(query(**{"openid.claimed_id": claimed}), BASE, opener=steam) is None


def test_steam_being_unreachable_is_a_no_not_a_yes():
    def broken(request, timeout=None):
        raise OSError("steam is down")

    assert auth.verify(query(), BASE, opener=broken) is None


def test_the_database_never_holds_the_session_token(tmp_path):
    conn = store.connect(str(tmp_path))
    token = auth.new_token()
    store.put_user(conn, STEAM_ID, {"display_name": "Alfr"}, 100.0)
    store.start_session(conn, auth.token_hash(token), STEAM_ID, 100.0, 200.0)

    rows = conn.execute("SELECT token_hash FROM sessions").fetchall()
    assert token not in [r["token_hash"] for r in rows]
    assert store.session_user(conn, auth.token_hash(token), 150.0)["display_name"] == "Alfr"


def test_a_session_expires(tmp_path):
    conn = store.connect(str(tmp_path))
    token = auth.new_token()
    store.put_user(conn, STEAM_ID, {}, 100.0)
    store.start_session(conn, auth.token_hash(token), STEAM_ID, 100.0, 200.0)
    assert store.session_user(conn, auth.token_hash(token), 199.0) is not None
    assert store.session_user(conn, auth.token_hash(token), 201.0) is None


def test_signing_out_ends_the_session(tmp_path):
    conn = store.connect(str(tmp_path))
    token = auth.new_token()
    store.put_user(conn, STEAM_ID, {}, 100.0)
    store.start_session(conn, auth.token_hash(token), STEAM_ID, 100.0, 1e12)
    store.end_session(conn, auth.token_hash(token))
    assert store.session_user(conn, auth.token_hash(token), 150.0) is None


def test_the_cookie_is_locked_down():
    header = auth.cookie_header("abc", secure=True)
    assert "HttpOnly" in header and "SameSite=Lax" in header and "Secure" in header
    # Over plain http, Secure would make the cookie useless, so it is left off.
    assert "Secure" not in auth.cookie_header("abc", secure=False)


def test_sign_in_is_off_until_there_is_an_address_to_return_to():
    assert config.Config().steam_login is False
    assert config.Config(base_url=BASE).steam_login is True
    assert config.Config(base_url="http://box.lan:8080").secure_cookies is False
    assert config.Config(base_url=BASE).secure_cookies is True


def signed_in(tracker, monkeypatch, display="Alfr"):
    """A tracker whose config has sign-in on, plus a live session token."""
    cfg = config.replace(app.CONFIG, base_url=BASE)
    app.apply_config(cfg)
    token = auth.new_token()
    when = 1_800_000_000
    store.put_user(app.db(), STEAM_ID, {"display_name": display}, when)
    store.start_session(app.db(), auth.token_hash(token), STEAM_ID, when, when + 1e6)
    monkeypatch.setattr(app.time, "time", lambda: when + 10)
    return token


class Headers(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def test_the_page_offers_sign_in_only_when_configured(tracker):
    h = app.history()
    assert "sign in through Steam" not in app.render(h, 1_800_000_000, WORLD)
    app.apply_config(config.replace(app.CONFIG, base_url=BASE))
    assert "sign in through Steam" in app.render(app.history(), 1_800_000_000, WORLD)


def test_the_page_greets_whoever_is_signed_in(tracker, monkeypatch):
    token = signed_in(tracker, monkeypatch)
    user = app.current_user(Headers({"Cookie": f"{auth.COOKIE}={token}; other=x"}))
    assert user["display_name"] == "Alfr"
    page = app.render(app.history(), 1_800_000_000, WORLD, user)
    assert "signed in as" in page and "Alfr" in page and "sign out" in page


def test_a_bogus_cookie_is_nobody(tracker, monkeypatch):
    signed_in(tracker, monkeypatch)
    assert app.current_user(Headers({"Cookie": f"{auth.COOKIE}=made-up"})) is None
    assert app.current_user(Headers({})) is None
