"""Steam's two VDF flavours.

Binary (shortcuts.vdf): a byte tag per value --
  0x00 nested map (key, children..., 0x08), 0x01 string, 0x02 int32 LE, 0x03 float32,
  0x07 uint64, 0x0A int64, 0x08 end of map.
Values keep their wire type (Int32 / UInt64 / ...) so an unmodified file writes back
byte-identical -- shortcuts other tools made are never disturbed.

Text (loginusers.vdf, libraryfolders.vdf): quoted key/value pairs and { } blocks.
"""
from __future__ import annotations

import struct


class Int32(int):
    pass


class UInt64(int):
    pass


class Int64(int):
    pass


class Float32(float):
    pass


class VdfError(ValueError):
    pass


def loads_binary(data: bytes) -> dict:
    pos = 0

    def cstr() -> str:
        nonlocal pos
        end = data.find(b"\x00", pos)
        if end < 0:
            raise VdfError(f"unterminated string at {pos}")
        s = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return s

    def read_map() -> dict:
        nonlocal pos
        out: dict = {}
        while True:
            if pos >= len(data):
                raise VdfError("unexpected end of file")
            t = data[pos]
            pos += 1
            if t == 0x08:
                return out
            key = cstr()
            if t == 0x00:
                out[key] = read_map()
            elif t == 0x01:
                out[key] = cstr()
            elif t == 0x02:
                out[key] = Int32(struct.unpack_from("<i", data, pos)[0]); pos += 4
            elif t == 0x03:
                out[key] = Float32(struct.unpack_from("<f", data, pos)[0]); pos += 4
            elif t == 0x07:
                out[key] = UInt64(struct.unpack_from("<Q", data, pos)[0]); pos += 8
            elif t == 0x0A:
                out[key] = Int64(struct.unpack_from("<q", data, pos)[0]); pos += 8
            else:
                raise VdfError(f"unknown type 0x{t:02x} at {pos - 1}")

    root = read_map()
    # Files end with one extra 0x08 closing the implicit root.
    return root


def dumps_binary(obj: dict) -> bytes:
    out = bytearray()

    def cstr(s: str):
        out.extend(s.encode("utf-8"))
        out.append(0)

    def write_map(m: dict):
        for k, v in m.items():
            if isinstance(v, dict):
                out.append(0x00); cstr(k); write_map(v)
            elif isinstance(v, str):
                out.append(0x01); cstr(k); cstr(v)
            elif isinstance(v, Float32) or isinstance(v, float):
                out.append(0x03); cstr(k); out.extend(struct.pack("<f", v))
            elif isinstance(v, UInt64):
                out.append(0x07); cstr(k); out.extend(struct.pack("<Q", v))
            elif isinstance(v, Int64):
                out.append(0x0A); cstr(k); out.extend(struct.pack("<q", v))
            elif isinstance(v, int):
                iv = int(v)
                if iv >= 2**31:  # appid is stored as signed int32 on the wire
                    iv -= 2**32
                out.append(0x02); cstr(k); out.extend(struct.pack("<i", iv))
            else:
                raise VdfError(f"can't encode {k}={v!r}")
        out.append(0x08)

    write_map(obj)
    return bytes(out)


def loads_text(text: str) -> dict:
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c in "{}":
            tokens.append(c); i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, nxt)); j += 2
                else:
                    buf.append(text[j]); j += 1
            tokens.append(("s", "".join(buf))); i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}"':
                j += 1
            tokens.append(("s", text[i:j])); i = j

    pos = 0

    def parse_block() -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            t = tokens[pos]
            if t == "}":
                pos += 1
                return out
            if t == "{":
                raise VdfError("unexpected {")
            key = t[1]; pos += 1
            if pos < len(tokens) and tokens[pos] == "{":
                pos += 1
                out[key] = parse_block()
            elif pos < len(tokens) and isinstance(tokens[pos], tuple):
                out[key] = tokens[pos][1]; pos += 1
            else:
                out[key] = ""
        return out

    return parse_block()
