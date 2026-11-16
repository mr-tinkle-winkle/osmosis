"""LittleBigPlanet online play on RPCS3 (custom servers: Beacon, Bonsai).

How it fits together (from Refresher's source, the patcher LBP Union approves):
  * Refresher's "RPCS3 Patch" decrypts the game's EBOOT, adds the Patchwork SPRX
    module, and re-signs it. The original is kept as USRDIR/EBOOT.BIN.ORIG.
    That step needs Refresher itself and is never done here.
  * Patchwork reads its settings from dev_hdd0/plugins/patchwork/:
      patchwork_url.txt            server URL the game talks to
      patchwork_digest.txt         "CustomServerDigest" if the server uses a custom digest key
      patchwork_lobby_password.txt optional join key; only players with the same key share sessions
    Those files are shared by every patched LBP game, so changing servers is just
    rewriting them -- which osmosis does.
  * Servers describe themselves at <base>/autodiscover (url, usesCustomDigestKey, ...).
  * RPCS3 itself needs Net > "Internet enabled: Connected" and "PSN status: RPCN",
    plus an RPCN account (config/rpcn.yml NPID).
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from . import config
from .rpcs3 import Game, Rpcs3

SERVERS = {
    "beacon": dict(name="Beacon", by="LBP Union", base="https://beacon.lbpunion.com",
                   site="https://beacon.lbpunion.com",
                   account="Sign up on the Beacon website, then verify your RPCN name under its Authentication tab."),
    "bonsai": dict(name="Bonsai", by="formerly Refresh", base="https://lbp.lbpbonsai.com",
                   site="https://lbp.lbpbonsai.com",
                   account="Register an account on the Bonsai website using your RPCN name before connecting."),
}
REFRESHER_RELEASES = "https://github.com/LittleBigRefresh/Refresher/releases"
REFRESHER_GUIDE = "https://docs.littlebigrefresh.com/rpcs3"

_WHITELIST = ("littlebigplanet", "lbp", "リトルビッグプラネット", "리틀 빅 플래닛")
_EXCLUDE = ("karting", "manual")


def is_lbp(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in _WHITELIST) and not any(x in t for x in _EXCLUDE)


@dataclass
class LbpGame:
    game: Game
    eboot: Path | None       # dev_hdd0/game/<serial>/USRDIR/EBOOT.BIN
    patched: bool            # Refresher left EBOOT.BIN.ORIG behind

    @property
    def serial(self):
        return self.game.serial


def plugins_dir(rp: Rpcs3) -> Path:
    return rp.dev_hdd0 / "plugins"


def games(rp: Rpcs3, all_games: list[Game]) -> list[LbpGame]:
    out = []
    for g in all_games:
        if not is_lbp(g.title):
            continue
        usr = rp.dev_hdd0 / "game" / g.serial / "USRDIR"
        eb = usr / "EBOOT.BIN"
        out.append(LbpGame(g, eb if eb.is_file() else None, (usr / "EBOOT.BIN.ORIG").is_file()))
    return out


def patchwork_installed(rp: Rpcs3) -> bool:
    return (plugins_dir(rp) / "patchwork.sprx").is_file()


def _pw(rp: Rpcs3, name: str) -> Path:
    return plugins_dir(rp) / "patchwork" / name


def current_server(rp: Rpcs3) -> tuple[str | None, str | None]:
    """(server key or None, raw url or None)."""
    try:
        url = _pw(rp, "patchwork_url.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    for key, s in SERVERS.items():
        host = s["base"].split("//", 1)[1]
        if host in url:
            return key, url
    return "custom", url


def lobby_password(rp: Rpcs3) -> str:
    try:
        return _pw(rp, "patchwork_lobby_password.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def autodiscover(base: str, timeout: float = 10.0, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(base.rstrip("/") + "/autodiscover", headers={"User-Agent": "osmosis"})
    with opener(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("url"):
        raise ValueError("server didn't say which URL to patch to")
    return data


def set_server(rp: Rpcs3, key: str, password: str | None = None, discovered: dict | None = None) -> str:
    """Point Patchwork at a server. Returns the URL written."""
    if not patchwork_installed(rp):
        raise RuntimeError("Patchwork isn't installed yet: patch a LittleBigPlanet game with Refresher first.")
    info = discovered or autodiscover(SERVERS[key]["base"])
    d = _pw(rp, "x").parent
    d.mkdir(parents=True, exist_ok=True)
    bak = config.data_dir() / "lbp-backups" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if any(d.iterdir()):
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(d, bak)
    (d / "patchwork_url.txt").write_text(info["url"], encoding="utf-8")
    dig = d / "patchwork_digest.txt"
    if info.get("usesCustomDigestKey"):
        dig.write_text("CustomServerDigest", encoding="utf-8")
    else:
        dig.unlink(missing_ok=True)
    if password is not None:
        pw = d / "patchwork_lobby_password.txt"
        if password.strip():
            pw.write_text(password.strip(), encoding="utf-8")
        else:
            pw.unlink(missing_ok=True)
    return info["url"]


def restore_original(lg: LbpGame) -> None:
    """Undo Refresher's patch for one game (puts EBOOT.BIN.ORIG back)."""
    usr = lg.eboot.parent if lg.eboot else None
    if not usr or not (usr / "EBOOT.BIN.ORIG").is_file():
        raise RuntimeError("No original EBOOT backup found for this game.")
    shutil.copy2(usr / "EBOOT.BIN.ORIG", usr / "EBOOT.BIN")
    (usr / "EBOOT.BIN.ORIG").unlink()


# -- RPCS3 network + RPCN ------------------------------------------------------------
@dataclass
class NetStatus:
    internet: bool
    rpcn: bool
    account: str      # RPCN NPID ("" = no account set up)


def net_status(rp: Rpcs3) -> NetStatus:
    try:
        cfg = yaml.safe_load(rp.yml("config.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    net = cfg.get("Net") or {}
    try:
        rpcn = yaml.safe_load(rp.yml("rpcn.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        rpcn = {}
    return NetStatus(str(net.get("Internet enabled", "")) == "Connected", str(net.get("PSN status", "")) == "RPCN",
                     str(rpcn.get("NPID") or ""))


_NET_WANTED = {"Internet enabled": "Connected", "PSN status": "RPCN"}


def enable_network(rp: Rpcs3) -> Path | None:
    """Set Internet enabled: Connected and PSN status: RPCN in config.yml, editing only
    those lines so the rest of the file (and RPCS3's own formatting) stays untouched.
    Returns the backup path."""
    path = rp.yml("config.yml")
    backup = None
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        backup = config.data_dir() / "rpcs3-config-backups" / f"config-{datetime.now():%Y-%m-%d_%H%M%S}.yml"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    else:
        text = ""
        path.parent.mkdir(parents=True, exist_ok=True)
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if re.match(r"^Net:\s*$", l))
    except StopIteration:
        lines += ["Net:"] + [f"  {k}: {v}" for k, v in _NET_WANTED.items()]
    else:
        end = start + 1
        while end < len(lines) and (lines[end].startswith(" ") or not lines[end].strip()):
            end += 1
        done = set()
        for i in range(start + 1, end):
            m = re.match(r"^(\s+)([^:#]+):\s*(.*)$", lines[i])
            if m and m.group(2).strip() in _NET_WANTED:
                k = m.group(2).strip()
                lines[i] = f"{m.group(1)}{k}: {_NET_WANTED[k]}"
                done.add(k)
        indent = "  "
        for i in range(start + 1, end):
            m = re.match(r"^(\s+)\S", lines[i])
            if m:
                indent = m.group(1)
                break
        extra = [f"{indent}{k}: {v}" for k, v in _NET_WANTED.items() if k not in done]
        lines[end:end] = extra
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return backup


def old_server_patches(rp: Rpcs3) -> list[str]:
    """Names of enabled RPCS3 game patches that also redirect LBP's server (the
    pre-Patchwork method); they're best switched off once Patchwork is in use."""
    p = rp.yml("patch_config.yml")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    found = set()

    def walk(node, name=None):
        if isinstance(node, dict):
            if node.get("Enabled") is True and name and "server" in name.lower():
                found.add(name)
            for k, v in node.items():
                walk(v, k if isinstance(k, str) and ("server" in k.lower() or name is None) else name)

    for _hash, patches in data.items():
        if isinstance(patches, dict):
            for pname, body in patches.items():
                if isinstance(pname, str) and "server" in pname.lower():
                    walk(body, pname)
    return sorted(found)


def find_refresher(settings: config.Settings) -> str | None:
    if getattr(settings, "refresher_path", ""):
        p = Path(settings.refresher_path).expanduser()
        if p.exists():
            return str(p)
    for name in ("refresher", "Refresher", "Refresher.AppImage"):
        w = shutil.which(name)
        if w:
            return w
    for c in (Path.home() / "Applications", Path.home() / ".local/bin", Path.home() / "Downloads"):
        if c.is_dir():
            for f in c.glob("Refresher*"):
                if f.is_file() and f.stat().st_mode & 0o111:
                    return str(f)
    return None
