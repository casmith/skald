"""Write a .fch the way a real one is written, for tests.

Checked against actual character files: the three optional points each
occupy thirteen bytes whether they hold anything or not, and the map is a
length-prefixed byte array rather than inline fields. Getting either wrong
is how a parser reads two worlds as the same one.
"""
import struct


def s(text):
    b = text.encode()
    n, out = len(b), bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        out.append(x | (0x80 if n else 0))
        if not n:
            break
    return bytes(out) + b


def point(xyz=None):
    """A point: present flag, then twelve bytes either way."""
    if xyz is None:
        return b"\x00" + bytes(12)
    return b"\x01" + struct.pack("<3f", *xyz)


def character(worlds, version=46, legacy_map=False):
    d = bytearray()
    d += struct.pack("<i", version)
    d += struct.pack("<4i", 1, 2, 3, 4)
    d += struct.pack("<i", len(worlds))
    for w in worlds:
        d += struct.pack("<q", w["uid"])
        d += point(w.get("spawn")) + point(w.get("logout")) + point(w.get("death"))
        d += struct.pack("<3f", 0.0, 0.0, 0.0)          # home point
        if "explored" not in w:
            d += b"\x00"
            continue
        d += b"\x01"
        body = bytearray()
        body += struct.pack("<i", 4)                     # map version
        body += struct.pack("<i", w["edge"])
        body += bytes(w["explored"])
        body += struct.pack("<i", len(w.get("pins", [])))
        for p in w.get("pins", []):
            body += s(p["name"])
            body += struct.pack("<3f", p["x"], 0.0, p["z"])
            body += struct.pack("<i", p["type"])
            body += bytes([1 if p.get("crossed") else 0])
        body += b"\x00"                                 # position not shared
        if not legacy_map:
            d += struct.pack("<i", len(body))            # the byte-array length
        d += body
    d += s("Somebody") + struct.pack("<q", 1234) + s("seed") + b"\x00"
    return struct.pack("<i", len(d)) + bytes(d) + struct.pack("<i", 64) + b"\x00" * 64
