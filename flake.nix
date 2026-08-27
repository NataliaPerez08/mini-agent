{
  description = "mini-opencode-mvp dev shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python311
            python311Packages.pip
            python311Packages.virtualenv
            ripgrep
            git
          ];

          shellHook = ''
            echo "mini-opencode-mvp"
            echo "Python: $(python --version)"
            echo "Run: python -m venv .venv && source .venv/bin/activate && pip install -e ."
          '';
        };
      });
}
