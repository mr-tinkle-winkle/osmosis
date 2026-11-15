"""Minimal SteamGridDB v2 client (needs a free API key from steamgriddb.com/profile/preferences/api)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.steamgriddb.com/api/v2"
UA = "osmosis (RPCS3 Steam sync)"

# Steam grid slots -> (endpoint, query params)
SLOTS = {
    "cover": ("grids", {"dimensions": "600x900,342x482,660x930", "mimes": "image/png,image/jpeg", "types": "static"}),
    "wide": ("grids", {"dimensions": "920x430,460x215", "mimes": "image/png,image/jpeg", "types": "static"}),
    "hero": ("heroes", {"mimes": "image/png,image/jpeg", "types": "static"}),
    "logo": ("logos", {"mimes": "image/png", "types": "static"}),
    "icon": ("icons", {"mimes": "image/png", "types": "static"}),
}


class SgdbError(RuntimeError):
    pass


class SteamGridDB:
    def __init__(self, api_key: str, timeout: float = 15.0, opener=None):
        self.key = api_key.strip()
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _get(self, path: str, params: dict | None = None):
        url = f"{API}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.key}", "User-Agent": UA})
        try:
            with self._open(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SgdbError("SteamGridDB rejected the API key") from e
            if e.code == 404:
                return []
            raise SgdbError(f"SteamGridDB error {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SgdbError(f"couldn't reach SteamGridDB: {e}") from e
        if not body.get("success", False):
            return []
        return body.get("data") or []

    def find_game(self, title: str) -> int | None:
        res = self._get(f"search/autocomplete/{urllib.parse.quote(title, safe='')}")
        return res[0]["id"] if res else None

    def best(self, game_id: int, slot: str) -> str | None:
        endpoint, params = SLOTS[slot]
        res = self._get(f"{endpoint}/game/{game_id}", params)
        for r in res:
            if r.get("url") and not r.get("nsfw"):
                return r["url"]
        return None

    def download(self, url: str) -> tuple[bytes, str]:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with self._open(req, timeout=self.timeout) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "") if hasattr(r, "headers") else ""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SgdbError(f"download failed: {e}") from e
        ext = ".jpg" if ("jpeg" in ctype or url.lower().endswith((".jpg", ".jpeg"))) else ".png"
        return data, ext
