{
  description = "yap - terminal LLM chat TUI";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = {
    self,
    nixpkgs,
    ...
  }: let
    supportedSystems = ["x86_64-linux"];
    pkgsBySystem = nixpkgs.lib.getAttrs supportedSystems nixpkgs.legacyPackages;
    forAllSystems = fn: nixpkgs.lib.mapAttrs fn pkgsBySystem;
  in {
    formatter = forAllSystems (system: pkgs: pkgs.alejandra);

    packages = forAllSystems (
      system: pkgs: let
        pythonWithPip = pkgs.python312.withPackages (ps: with ps; [pip]);

        # FOD: pre-download wheels
        deps = pkgs.stdenv.mkDerivation {
          name = "yap-deps";
          src = ./.;
          dontBuild = true;
          nativeBuildInputs = [pythonWithPip];
          installPhase = ''
            mkdir $out
            python3 -m pip download -r requirements.txt -d $out
          '';
          outputHash = "sha256-vLsXiGlaqtquhcc9fpWMH+nTXJruInewRPfz+wyXvEs=";
          outputHashMode = "recursive";
          outputHashAlgo = "sha256";
          dontFixup = true;
        };

        # Build: install from pre-downloaded wheels
        python = pkgs.stdenv.mkDerivation {
          name = "yap-python";
          src =
            pkgs.lib.filterSource (
              path: type: let
                name = pkgs.lib.baseNameOf path;
              in
                !pkgs.lib.elem name ["pyproject.toml" "uv.lock" ".git" "bin" "tests" "deps"]
            )
            ./.;
          dontBuild = true;
          nativeBuildInputs = [pythonWithPip];
          installPhase = ''
            python3 -m venv $out
            $out/bin/pip install --no-index --find-links=${deps} -r requirements.txt
          '';
        };

        yap = pkgs.writeShellScriptBin "yap" ''
          ${python}/bin/python ${./yap.py} "$@"
        '';
      in {
        inherit python deps;
        default = yap;
      }
    );

    devShells = forAllSystems (system: pkgs: {
      default = pkgs.mkShell {
        name = "yap-dev-shell";
        packages = [
          pkgs.uv
          pkgs.python312
          pkgs.ruff
          pkgs.python312.pkgs.pytest
        ];
        shellHook = ''
          echo "yap dev shell"
          echo "  uv sync        # install deps"
          echo "  ./yap.py       # run"
        '';
      };
    });
  };
}
