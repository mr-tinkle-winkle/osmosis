"""Just enough ISO 9660 to pull a PS3 game's menu files out of a disc image.

RPCS3 can boot a game straight from an .iso. The XMB files (PARAM.SFO, ICON0.PNG,
PIC1.PNG, SND0.AT3, ICON1.PAM) sit in the image's PS3_GAME folder, which is plain
ISO 9660 (PS3 disc dumps keep that area unencrypted). They are copied into
osmosis' cache once, and again only when the image changes.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

SECTOR = 2048
WANTED = ("PARAM.SFO", "ICON0.PNG", "PIC0.PNG", "PIC1.PNG", "PIC2.PNG", "PIC3.PNG")
WANTED_PREFIX = ("SND0", "ICON1")        # SND0.AT3, SND0_xx.AT3, ICON1.PAM, ICON1_xx.PAM


class IsoError(Exception):
    pass


def _records(f, lba: int, size: int):
    """(name, lba, size, is_dir) for every entry in a directory extent."""
    f.seek(lba * SECTOR)
    data = f.read(size)
    pos = 0
    while pos < len(data):
        n = data[pos]
        if n == 0:                               # rest of this sector is padding
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        rec = data[pos:pos + n]
        if len(rec) < 34:
            break
        ext = struct.unpack_from("<I", rec, 2)[0]
        length = struct.unpack_from("<I", rec, 10)[0]
        flags = rec[25]
        nlen = rec[32]
        raw = rec[33:33 + nlen]
        pos += n
        if raw in (b"\x00", b"\x01"):            # . and ..
            continue
        name = raw.decode("ascii", "replace").split(";")[0].rstrip(".")
        yield name, ext, length, bool(flags & 0x02)


def _root(f):
    f.seek(16 * SECTOR)
    pvd = f.read(SECTOR)
    if pvd[1:6] != b"CD001":
        raise IsoError("not an ISO 9660 image")
    root = pvd[156:156 + 34]
    return struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0]


def list_ps3_game(iso: Path) -> dict[str, tuple[int, int]]:
    """{UPPERCASE NAME: (lba, size)} of the files directly inside PS3_GAME."""
    with open(iso, "rb") as f:
        lba, size = _root(f)
        for name, ext, length, is_dir in _records(f, lba, size):
            if is_dir and name.upper() == "PS3_GAME":
                return {n.upper(): (e, ln) for n, e, ln, d in _records(f, ext, length) if not d}
    raise IsoError("no PS3_GAME folder in the image")


def extract_menu_files(iso: Path, out: Path) -> Path:
    """Copy the menu files into `out` (a cache folder) and return it. Cheap when
    the image hasn't changed since last time."""
    st = iso.stat()
    out.mkdir(parents=True, exist_ok=True)
    stamp = {"size": st.st_size, "mtime": int(st.st_mtime)}
    marker = out / ".source.json"
    try:
        if json.loads(marker.read_text()) == stamp:
            return out
    except (OSError, ValueError):
        pass
    files = list_ps3_game(iso)
    with open(iso, "rb") as f:
        for name, (lba, size) in files.items():
            if name in WANTED or name.startswith(WANTED_PREFIX):
                f.seek(lba * SECTOR)
                (out / name).write_bytes(f.read(size))
    marker.write_text(json.dumps(stamp))
    return out
