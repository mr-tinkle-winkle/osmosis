{
  description = "osmosis - RPCS3 <-> Steam companion: library sync, save/trophy backups, profile";

  inputs = {
    # Overridden by the parent flake's `inputs.nixpkgs.follows = "nixpkgs"`.
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ];

      mkOsmosis = pkgs:
        let
          python = pkgs.python3;
        in
        python.pkgs.buildPythonApplication {
          pname = "osmosis";
          version = "0.1.0";
          format = "pyproject";
          src = ./.;

          nativeBuildInputs = [ python.pkgs.setuptools pkgs.qt6.wrapQtAppsHook ];
          buildInputs = [
            pkgs.qt6.qtbase
            pkgs.qt6.qtwayland
            pkgs.qt6.qtmultimedia   # theme sounds (QSoundEffect); without it the app is silent
            pkgs.qt6.qtsvg
            pkgs.qt6.qtimageformats # .jpg/.webp art from SteamGridDB in the library view
            pkgs.xcb-util-cursor
          ];
          propagatedBuildInputs = with python.pkgs; [ pyyaml tomli-w pyside6 ];

          nativeCheckInputs = [ python.pkgs.pytestCheckHook ];
          preCheck = ''
            export QT_QPA_PLATFORM=offscreen
            export HOME=$TMPDIR
          '';
          pythonImportsCheck = [ "osmosis" ];

          postInstall = ''
            install -Dm644 data/osmosis.desktop $out/share/applications/osmosis.desktop
            install -Dm644 data/osmosis.svg $out/share/icons/hicolor/scalable/apps/osmosis.svg
          '';

          # wrapQtApp explicitly: the hook doesn't reliably wrap Python entry-point scripts.
          dontWrapQtApps = true;
          postFixup = ''
            # pw-play/paplay play theme sounds; ffmpeg converts the PS3's menu music (ATRAC3)
            # and preview videos (PAMF) for Ribbon.
            wrapQtApp "$out/bin/osmosis" \
              --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.pipewire pkgs.pulseaudio pkgs.ffmpeg ]}
          '';

          meta = {
            description = "RPCS3 <-> Steam companion";
            homepage = "https://github.com/mr-tinkle-winkle/osmosis";
            platforms = pkgs.lib.platforms.linux;
            mainProgram = "osmosis";
          };
        };
    in
    {
      packages = forAllSystems (system: {
        default = mkOsmosis (import nixpkgs { inherit system; });
      });

      apps = forAllSystems (system:
        let pkg = self.packages.${system}.default;
        in {
          default = { type = "app"; program = "${pkg}/bin/osmosis"; };
          cli = { type = "app"; program = "${pkg}/bin/osmosis-cli"; };
        });

      devShells = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: [ ps.pyyaml ps.tomli-w ps.pyside6 ps.pytest ]))
              pkgs.qt6.qtbase pkgs.qt6.qtwayland pkgs.qt6.qtmultimedia
            ];
            shellHook = ''
              export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/lib/qt-6/plugins:${pkgs.qt6.qtwayland}/lib/qt-6/plugins:${pkgs.qt6.qtmultimedia}/lib/qt-6/plugins''${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
            '';
          };
        });

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.osmosis;
          pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        in
        {
          options.services.osmosis = {
            enable = lib.mkEnableOption "osmosis (RPCS3 <-> Steam companion)";
            user = lib.mkOption {
              type = lib.types.str;
              description = "User whose RPCS3 and Steam folders osmosis watches.";
            };
            watcher = lib.mkOption {
              type = lib.types.bool;
              default = true;
              description = ''
                Run the play-session watcher as a per-user service, so sessions are
                recorded (and session/daily/weekly auto-exports run) even while the
                osmosis window is closed.
              '';
            };
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ pkg ];
            systemd.user.services.osmosis-watcher = lib.mkIf cfg.watcher {
              description = "osmosis play-session watcher";
              wantedBy = [ "default.target" ];
              unitConfig.ConditionUser = cfg.user;
              serviceConfig = {
                ExecStart = "${pkg}/bin/osmosis-watcher";
                Restart = "on-failure";
                RestartSec = 5;
              };
            };
          };
        };
    };
}
