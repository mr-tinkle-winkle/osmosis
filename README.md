# osmosis

A companion for [RPCS3](https://rpcs3.net) on Linux that keeps a PS3 library, its saves and its trophies in step with Steam and safe from reinstalls.

- **Steam library sync.** Every RPCS3 game becomes a Steam shortcut that boots straight into the game. Each one gets full library art from SteamGridDB: cover, wide capsule, the hero banner above the title, logo and icon. The game's own PS3 art is the fallback. Shortcuts made with RPCS3's built-in *Create Steam Shortcut* are recognised and adopted rather than duplicated. Shortcuts for removed games are cleaned up.
- **Backups that survive a reinstall.** *Export all*, *Export data* and *Export trophies* each produce a single zip. Saves, PSN licences, settings, per-game configs and play history are included. Installed games and firmware are not. Restoring asks per game when something differs, and offers *replace all* and *skip all*. Anything overwritten is kept in a safety folder first. An optional auto-export copies chosen parts to chosen folders after every session, daily or weekly.
- **Profile.** Games played, time per game, trophy progress by grade, and a per-game timeline of play sessions and trophy unlocks.

## Themes

Ribbon (default), General (light/dark), Boilerworks, Trophy Hall, Shelf, Summit (light/dark),
Card Catalog (light/dark) and Terminal. Each has its own wording, background, timeline style and
synthesised sound set.

## Run

```
nix run github:mr-tinkle-winkle/osmosis          # the window
nix run github:mr-tinkle-winkle/osmosis#cli -- status
```

## CLI

```
osmosis-cli status
osmosis-cli games
osmosis-cli timeline BLUS30443
osmosis-cli export all|data|trophies [--out DIR]
osmosis-cli import BACKUP.zip [--replace-all | --skip-all | --dry-run]
osmosis-cli steam-sync [--dry-run] [--refresh-art] [--no-close]
osmosis-cli watch
```

`steam-sync` closes Steam when needed, because Steam rewrites `shortcuts.vdf` on exit, and reopens it afterwards. The previous `shortcuts.vdf` is saved under `~/.local/share/osmosis/steam-backups/`.

Art fetching needs a free SteamGridDB API key, set as `steamgriddb_api_key` in `~/.config/osmosis/config.toml`.

## NixOS

```nix
inputs.osmosis.url = "github:mr-tinkle-winkle/osmosis";
inputs.osmosis.inputs.nixpkgs.follows = "nixpkgs";
# ...
imports = [ inputs.osmosis.nixosModules.default ];
programs.osmosis.enable = true;   # also runs the per-user play-session watcher
```
