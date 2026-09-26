"""Write a .fch the way the format says one is written, for tests."""
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


def character(worlds, version=33):
    d = bytearray()
    d += struct.pack("<i", version)
    d += struct.pack("<4i", 1, 2, 3, 4)          # kills deaths crafts builds
    d += struct.pack("<i", len(worlds))
    for w in worlds:
        d += struct.pack("<q", w["uid"])
        d += b"\x00\x00\x00"                      # no spawn/logout/death point
        d += struct.pack("<3f", 0.0, 0.0, 0.0)    # home point
        if "explored" not in w:
            d += b"\x00"
            continue
        d += b"\x01"
        d += struct.pack("<i", 4)                 # map version
        d += struct.pack("<i", w["edge"])
        d += bytes(w["explored"])
        d += struct.pack("<i", len(w.get("pins", [])))
        for p in w.get("pins", []):
            d += s(p["name"])
            d += struct.pack("<3f", p["x"], 0.0, p["z"])
            d += struct.pack("<i", p["type"])
            d += bytes([1 if p.get("crossed") else 0])
        d += b"\x00"                              # position not shared
    # Then the player: a name, an id, a seed -- none of which Skald reads.
    d += s("Somebody") + struct.pack("<q", 1234) + s("seed") + b"\x00"
    return struct.pack("<i", len(d)) + bytes(d) + struct.pack("<i", 64) + b"\x00" * 64
