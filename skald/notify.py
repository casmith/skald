"""Telling people what happened, through a Discord webhook.

Discord is where these groups already are, so a webhook needs no account,
no bot, no gateway and no dependency -- one POST of one JSON field.

**Only Discord webhook URLs are accepted.** A signed-in visitor choosing
where the server sends a request is a request-forgery hole by construction:
without this, anyone with a Steam account could point Skald at a machine
behind its firewall and use it as a prod. Restricting the host to Discord's
own webhook endpoint closes that, at the cost of not supporting other
targets -- which is the right trade for a page anyone can sign in to.
"""
import json
import urllib.parse
import urllib.request

ALLOWED_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com",
                 "canary.discord.com")
# Raids join this once raid history lands; nothing here should offer
# a notification that nothing sends.
KINDS = ("online", "milestone")
TIMEOUT = 10


def check_url(url):
    """The URL if Skald will post to it, else None. Nothing else is tried."""
    url = (url or "").strip()
    try:
        bits = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if bits.scheme != "https" or bits.hostname not in ALLOWED_HOSTS:
        return None
    if not bits.path.startswith("/api/webhooks/"):
        return None
    return url


def masked(url):
    """Enough to recognise it, not enough to post with."""
    bits = urllib.parse.urlsplit(url or "")
    tail = bits.path.rsplit("/", 1)[-1]
    return f"{bits.hostname}/…/{tail[:6]}…" if tail else (bits.hostname or "")


def send(url, text, opener=urllib.request.urlopen):
    """Post one message. Raises on anything that is not a success."""
    body = json.dumps({"content": text[:1900]}).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with opener(request, timeout=TIMEOUT) as response:
        code = getattr(response, "status", None) or response.getcode()
        if code >= 300:
            raise OSError(f"webhook answered {code}")
        return code
