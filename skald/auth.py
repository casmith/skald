"""Sign in through Steam.

Steam is an OpenID 2.0 provider, not an OAuth2 one: there is no client
secret, no registration and no approval. A visitor is sent to Steam, comes
back with a claimed identity in the query string, and **that identity means
nothing until Steam confirms it** -- the parameters are user-supplied and
trivially forged. Confirming is one POST back to Steam, and it is the whole
security of this.

What comes back is a SteamID64 and nothing else. A display name and avatar
need a Steam Web API key, which is free and optional; without one, people
are known by the character they claim.

Sessions are a random token in a cookie. The database keeps only its hash,
so a stolen copy of the database cannot be used to sign in as anyone.
"""
import hashlib
import re
import secrets
import time
import urllib.parse
import urllib.request

STEAM_OPENID = "https://steamcommunity.com/openid/login"
NS = "http://specs.openid.net/auth/2.0"
IDENTIFIER_SELECT = f"{NS}/identifier_select"
# Steam returns the identity as https://steamcommunity.com/openid/id/<id64>
CLAIMED_ID_RE = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{17})$")
COOKIE = "skald_session"
SESSION_DAYS = 30


def login_url(base_url):
    """Where to send someone who wants to sign in."""
    base_url = base_url.rstrip("/")
    return STEAM_OPENID + "?" + urllib.parse.urlencode({
        "openid.ns": NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": f"{base_url}/auth/callback",
        "openid.realm": base_url,
        "openid.identity": IDENTIFIER_SELECT,
        "openid.claimed_id": IDENTIFIER_SELECT,
    })


def verify(query, base_url, opener=urllib.request.urlopen):
    """The SteamID64 behind a callback, or None if Steam does not vouch for it.

    `query` is the callback's query string parameters. Everything in it is
    attacker-controlled, so nothing is believed until Steam says so.
    """
    params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    claimed = params.get("openid.claimed_id", "")
    found = CLAIMED_ID_RE.match(claimed)
    if not found:
        return None
    # The response must be for *this* site: a signed assertion made out to
    # somewhere else is not ours to accept.
    if params.get("openid.return_to", "") != f"{base_url.rstrip('/')}/auth/callback":
        return None

    # Ask Steam whether it really issued this. Same parameters, one word
    # changed; anything but a plain "is_valid:true" is a no.
    params["openid.mode"] = "check_authentication"
    body = urllib.parse.urlencode(params).encode()
    request = urllib.request.Request(
        STEAM_OPENID, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener(request, timeout=10) as response:
            answer = response.read().decode("utf-8", "replace")
    except Exception:
        return None
    if not re.search(r"^is_valid:true$", answer, re.M):
        return None
    return found.group(1)


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    """What the database stores: a stolen copy cannot sign in as anyone."""
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_header(token, secure, days=SESSION_DAYS):
    attrs = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={days * 86400}"]
    if secure:
        attrs.append("Secure")
    return "; ".join(attrs)


def clear_cookie_header(secure):
    attrs = [f"{COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        attrs.append("Secure")
    return "; ".join(attrs)


def token_from_cookies(header):
    """The session token in a Cookie header, if there is one."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and value:
            return value
    return None


def fetch_profile(steam_id, api_key, opener=urllib.request.urlopen):
    """Display name and avatar, if a Steam Web API key is configured.

    Optional by design: Skald works without one, and a deployment that would
    rather not talk to Steam at all beyond sign-in can leave it unset.
    """
    if not api_key:
        return {}
    url = ("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?"
           + urllib.parse.urlencode({"key": api_key, "steamids": steam_id}))
    try:
        import json
        with opener(url, timeout=10) as response:
            players = json.load(response)["response"]["players"]
    except Exception:
        return {}
    if not players:
        return {}
    player = players[0]
    return {"display_name": player.get("personaname") or "",
            "avatar": player.get("avatar") or ""}


def now():
    return time.time()
