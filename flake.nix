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
        let python = pkgs.python3;
        in python.pkgs.buildPythonApplication {
          pname = "osmosis";
          version = "0.1.0";
          format = "pyproject";
          src = ./.;
          nativeBuildInputs = [ python.pkgs.setuptools ];
          propagatedBuildInputs = with python.pkgs; [ pyyaml tomli-w ];
          nativeCheckInputs = [ python.pkgs.pytestCheckHook ];
          pythonImportsCheck = [ "osmosis" ];
          meta = {
            description = "RPCS3 <-> Steam companion";
            homepage = "https://github.com/mr-tinkle-winkle/osmosis";
            platforms = pkgs.lib.platforms.linux;
            mainProgram = "osmosis-cli";
          };
        };
    in
    {
      packages = forAllSystems (system: {
        default = mkOsmosis (import nixpkgs { inherit system; });
      });

      devShells = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = [ (pkgs.python3.withPackages (ps: [ ps.pyyaml ps.tomli-w ps.pytest ])) ];
          };
        });

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.programs.osmosis;
          pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        in
        {
          options.programs.osmosis = {
            enable = lib.mkEnableOption "osmosis (RPCS3 <-> Steam companion)";
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
