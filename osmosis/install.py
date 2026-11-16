"""Installing PS3 content you already have on disk into RPCS3.

* .rap / .edat licence files are copied into dev_hdd0/home/<user>/exdata/ (what RPCS3's
  "Install Packages/Raps/Edats" does with them).
* .pkg files are handed to RPCS3 itself (`rpcs3 --installpkg FILE`), one at a time:
  RPCS3 opens, installs, and osmosis moves on to the next when that RPCS3 window is closed.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .rpcs3 import Rpcs3

KINDS = {".pkg": "pkg", ".rap": "licence", ".edat": "licence"}


@dataclass
class Found:
    pkgs: list[Path] = field(default_factory=list)
    licences: list[Path] = field(default_factory=list)

    def __bool__(self):
        return bool(self.pkgs or self.licences)


def scan(paths: list[Path]) -> Found:
    out = Found()
    seen = set()
    for p in paths:
        p = Path(p).expanduser()
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else []
        for f in files:
            kind = KINDS.get(f.suffix.lower())
            if not kind or f.resolve() in seen:
                continue
            seen.add(f.resolve())
            (out.pkgs if kind == "pkg" else out.licences).append(f)
    return out


def install_licences(rp: Rpcs3, files: list[Path]) -> list[Path]:
    dst = rp.user_home / "exdata"
    dst.mkdir(parents=True, exist_ok=True)
    done = []
    for f in files:
        target = dst / f.name
        if target.is_file() and target.stat().st_size == f.stat().st_size:
            continue
        shutil.copy2(f, target)
        done.append(target)
    return done


def install_pkgs(rpcs3_exe: str, pkgs: list[Path], progress: Callable[[str], None]) -> list[tuple[Path, int]]:
    results = []
    for i, pkg in enumerate(pkgs, 1):
        progress(f"Installing {pkg.name} ({i} of {len(pkgs)}) — close RPCS3 once it says the install finished.")
        try:
            r = subprocess.run([rpcs3_exe, "--installpkg", str(pkg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            results.append((pkg, r.returncode))
        except OSError as e:
            progress(f"Couldn't start RPCS3: {e}")
            results.append((pkg, -1))
            break
    return results
