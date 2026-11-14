{
  description = "Mango Settings: a GNOME-style settings app for MangoWM";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        mango-settings = pkgs.callPackage ./packaging/nix/package.nix { };
        default = mango-settings;
      });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/mango-settings";
        };
      });

      overlays.default = final: _prev: {
        mango-settings = final.callPackage ./packaging/nix/package.nix { };
      };
    };
}
