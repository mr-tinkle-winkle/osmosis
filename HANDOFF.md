# osmosis — handoff

## What the application does

- Keeps RPCS3's PS3 library mirrored in Steam as shortcuts with full library art.
- Exports and imports saves, trophies, configs and play history as plain zips, so a reinstall or distro change loses nothing.
- Presents a Steam-style profile with per-game timelines.

Target: NixOS, KDE Plasma, nixpkgs RPCS3, nixpkgs Steam (both autodetected, with overrides in settings).

## Current state

**Done.** The backend, CLI, play-session watcher, flake and NixOS module are done. There are 18 tests (`python -m pytest`), run against fake RPCS3 and Steam trees built in `tests/fakes.py` that mirror the real layouts.

**GUI:** first version done (`osmosis/gui/`). It is built standalone, without the shared `qt_ui_kit`, and can be ported onto that kit once the kit is available. Offscreen smoke tests cover every theme × page (`tests/test_gui.py`).

| File | Role |
|---|---|
| `themes.py` | Colours, fonts, words, backdrop, timeline style and sound set per theme. Also holds the Ribbon month palette and time-of-day brightness. |
| `backdrop.py` | Painted backgrounds. Ribbon is animated: wave planes are rendered at 1/5 scale and upscaled, which blurs them. |
| `timeline.py` | Per-theme session/trophy timeline painters. |
| `sounds.py` | Synthesised WAVs, generated into the data dir on first use. |
| `widgets.py` | Game cards, hero banner (Steam hero/logo art when synced), trophy rows, toast, clipboard helper. |
| `app.py` | Window, pages (Profile, Library, game detail, Steam Sync, Backup, Settings), import conflict dialog, in-window watcher. The watcher runs only when the service doesn't hold the lock. |
| `fonts/` | Bundled OFL/Apache fonts. |
| `xmb.py` | Ribbon's library: a vertical list in the PS3-menu style. Holding on a game fades in its PIC1, plays SND0 and swaps the icon for ICON1. |
| `media.py` | Converts SND0.AT3 / ICON1.PAM with ffmpeg into a cache, then plays them through Qt Multimedia. Players are created lazily, never under offscreen Qt (creating QAudioOutput without an audio device segfaults). Untested against real PAM/AT3 files. |
| `osmosis/lbp.py` | LittleBigPlanet online. Detects LBP games and Refresher patch state (`USRDIR/EBOOT.BIN.ORIG`). Patchwork's shared settings live in `dev_hdd0/plugins/patchwork/` (`patchwork_url.txt`, `patchwork_digest.txt`, `patchwork_lobby_password.txt`). Servers are switched via `<base>/autodiscover`. RPCS3 network lines are edited in place (`Net: Internet enabled: Connected`, `PSN status: RPCN`). EBOOT decryption and SPRX injection stay with Refresher, the only patcher LBP Union approves. Servers: Beacon `https://beacon.lbpunion.com`, Bonsai (formerly Refresh) `https://lbp.lbpbonsai.com`. |

**Not verified.** `nix build`. The flake parses, but nixpkgs could not be fetched in the development sandbox. The first build on a real machine is the real check.

**Not seen.** A real RPCS3 or Steam install. Every format was taken from RPCS3's own source, cited in each module's docstring. Real-world surprises are still expected on the first run.

## Architecture

| File | Role |
|---|---|
| `osmosis/rpcs3.py` | Locates the config dir, `dev_hdd0` (via `vfs.yml`) and the active user. Reads `games.yml` and `dev_hdd0/game` (category HG = game; GD = update, ignored). Reads playtime and last-played from `GuiConfigs/persistent_settings.dat` (QSettings INI, ms). `find_rpcs3_binary()` prefers profile symlinks over `/nix/store` paths, so Steam shortcuts survive RPCS3 updates. |
| `osmosis/sfo.py` | PARAM.SFO reader. The writer is used for fixtures. |
| `osmosis/trophies.py` | Parses `TROPCONF.SFM` (XML) and `TROPUSR.DAT` (big-endian; tables 4 and 6; CellRtcTick µs since year 1 UTC). Completion % is points-weighted, platinum excluded. Sets are matched to games by NP comm id (game's `TROPDIR`) first, then by title. |
| `osmosis/library.py` | `load_profile()` combines games, trophy sets and session history. `GameEntry.timeline()` returns sessions and unlocks in order. |
| `osmosis/backup.py` | Export parts: saves (savedata + exdata licences + vmc), configs (config dir minus `dev_*`/cache/logs/games; osmosis `config.toml` with the API key stripped), trophies, history (SQLite snapshot). Import: `plan()` groups archive members into items, one per game (its save folders + trophy set), plus configs, licences, other and history. `apply()` asks per conflicting item: replace, skip, replace_all or skip_all. Save folders and trophy sets are replaced wholesale. Overwritten data moves to `~/.local/share/osmosis/pre-import/<stamp>/`. History is always merged. Refuses to run while RPCS3 is open. |
| `osmosis/vdf.py` | Binary (shortcuts.vdf, byte-exact round trip) and text (loginusers.vdf) VDF. |
| `osmosis/steam.py` | Shortcut sync. Launch options match RPCS3's own button (`--no-gui "%RPCS3_GAMEID%:SERIAL"`), so its shortcuts are adopted. New appids use `crc32(quoted exe + name) \| 0x80000000`. Existing appids are kept so linked art survives. Entries are tagged `osmosis` plus the collection tag; only `osmosis`-tagged entries are ever removed. Art slots: `<appid>p`, `<appid>`, `_hero`, `_logo`, `_icon`. Adopted RPCS3 shortcuts get their small PS3 art upgraded once when a key exists. Steam is closed and reopened around writes. |
| `osmosis/steamgriddb.py` | Minimal v2 client: autocomplete search, then the best static image per slot. |
| `osmosis/watcher.py` | Polls every 10 s. The game whose `[Playtime]` total grows is the one being played; `--no-gui` command lines identify the game before the first write. A session ends when RPCS3 exits or after 150 s without growth. Sessions shorter than 30 s are dropped. Session, daily and weekly auto-export triggers live here. Single instance via `flock`. |
| `osmosis/cli.py` | Every feature, scriptable. |

## Decisions and rationale

- **Default Steam art is RPCS3's own recipe** (`steam.rpcs3_style_art`, from `rpcs3qt/steam_utils.cpp`). PIC1 (else PIC3/PIC0/PIC2/ICON0) is used as-is for the wide capsule and hero. ICON0 is used as-is for the logo and icon, and centred on a 600x900 transparent canvas for the cover. RPCS3 has no CLI for creating shortcuts, so the recipe is reimplemented. SteamGridDB is opt-in (`steam.art_source`).
- **UI sounds play through pw-play/paplay** subprocesses. QSoundEffect is only a fallback.

- **Own sync rather than RPCS3's built-in button.** The button works one game at a time and uses only the game's small PS3 art. osmosis does the whole library, fetches proper art and cleans up removed games. It reuses the button's launch format so the two never collide.
- **Session history starts when osmosis starts watching.** RPCS3 stores only running totals. Trophy history is complete from day one because `TROPUSR.DAT` keeps unlock times.
- **Clipboard export carries a file, not text.** Saves can be hundreds of MB. The GUI is to put the zip on the clipboard as a file (`text/uri-list`), and optionally copy it to configured folders.

## Next up (rough priority)

1. **Real-machine feedback on the GUI**, then port to qt_ui_kit if wanted. Original spec, for reference:
   - Pages: Profile (default), Library, Steam Sync, Backup, Settings.
   - Profile and Backup carry three buttons: Export all, Export data, Export achievements/trophies. Each copies the zip to the clipboard.
   - Import dialog: per game, with Replace, Skip, Replace all and Skip all.
   - Settings: auto-export checkboxes (saves, configs, trophies, history), a trigger (never, after each session, daily, weekly), a list of folders, and a keep count.
   - Play button: launches `rpcs3 --no-gui "%RPCS3_GAMEID%:SERIAL"`.
   - The window runs the watcher in-process when the service isn't holding the lock.
   - Default theme: Ribbon.
2. **Themes.** Each has its own wording, animations and sounds:

   | Theme | Visual direction | Sounds |
   |---|---|---|
   | General (light + dark) | Neutral, KDE-native | — |
   | Ribbon | PS3-menu-style flowing translucent blurred wave planes, sparkles, highlight pulse, background colour by month | Soft menu ticks and chimes |
   | Boilerworks | Steampunk brass, pressure gauge | Hiss, valve clank |
   | Trophy Hall | Museum velvet, display cases | — |
   | Shelf | Game cases on a shelf, a pulled-out case shows details | — |
   | Summit (light + dark) | Topographic, games as peaks | — |
   | Card Catalog (light + dark) | Library card, typewriter, stamp | Typewriter, stamp |
   | Terminal | Green CRT, command-style buttons | Key ticks |

   All sounds are original; no console audio is used.
3. **Per-game timeline, themed.**
   - General: bars + dots
   - Ribbon: glowing nodes on the wave
   - Boilerworks: chart-recorder strip
   - Trophy Hall: corridor of dated plaques
   - Shelf: book chapters
   - Summit: days on the climb, camps, elevation from trophies
   - Card Catalog: due-date stamp history
   - Terminal: `git log` style
4. **Ribbon month palette.** Match the original monthly background colours (research pending).
5. **ISO games.** Read PARAM.SFO from inside `.iso` files. They are currently listed by serial only and skipped by Steam sync's art lookup.

## Open questions

- Whether a game launched with `--no-gui` writes playtime to `persistent_settings.dat` on the nixpkgs build. The watcher falls back to wall-clock time if it doesn't.
