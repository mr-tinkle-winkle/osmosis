"""PARAM.SFO (PSF) reader.

Layout (little-endian):
  header:  magic b"\\0PSF", version u32, key_table_start u32, data_table_start u32, entry_count u32
  entries: key_offset u16, fmt u16, len u32, max_len u32, data_offset u32   (16 bytes each)
fmt 0x0004 = utf8 (not NUL-terminated), 0x0204 = utf8 NUL-terminated, 0x0404 = u32.
"""
from __future__ import annotations

import struct
from pathlib import Path

MAGIC = b"\x00PSF"


class SfoError(ValueError):
    pass


def parse_sfo(data: bytes) -> dict[str, str | int]:
    if len(data) < 20 or data[:4] != MAGIC:
        raise SfoError("not a PARAM.SFO file")
    _version, key_start, data_start, count = struct.unpack_from("<IIII", data, 4)
    out: dict[str, str | int] = {}
    for i in range(count):
        off = 20 + i * 16
        if off + 16 > len(data):
            raise SfoError("truncated entry table")
        key_off, fmt, length, _max_len, data_off = struct.unpack_from("<HHIII", data, off)
        k0 = key_start + key_off
        k1 = data.find(b"\x00", k0)
        if k1 < 0:
            raise SfoError("unterminated key")
        key = data[k0:k1].decode("utf-8", "replace")
        d0 = data_start + data_off
        raw = data[d0:d0 + length]
        if fmt == 0x0404:
            out[key] = struct.unpack("<I", raw[:4])[0] if len(raw) >= 4 else 0
        else:
            out[key] = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return out


def read_sfo(path: Path) -> dict[str, str | int]:
    return parse_sfo(Path(path).read_bytes())


def build_sfo(values: dict[str, str | int]) -> bytes:
    """Writer -- used by tests/fixtures only."""
    keys = sorted(values)
    key_table = b""
    data_table = b""
    entries = b""
    for k in keys:
        v = values[k]
        if isinstance(v, int):
            raw, fmt, max_len = struct.pack("<I", v), 0x0404, 4
        else:
            raw = v.encode() + b"\x00"
            fmt, max_len = 0x0204, (len(raw) + 3) // 4 * 4
        entries += struct.pack("<HHIII", len(key_table), fmt, len(raw), max_len, len(data_table))
        key_table += k.encode() + b"\x00"
        data_table += raw.ljust(max_len, b"\x00")
    key_table = key_table.ljust((len(key_table) + 3) // 4 * 4, b"\x00")
    key_start = 20 + len(entries)
    data_start = key_start + len(key_table)
    header = MAGIC + struct.pack("<IIII", 0x0101, key_start, data_start, len(keys))
    return header + entries + key_table + data_table
