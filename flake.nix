{
  description = "Mango Settings — GTK3 settings manager for the MangoWM Wayland compositor";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems
        (system: f (import nixpkgs { inherit system; }));
    in
    {
      packages = forAllSystems (pkgs:
        let
          version = "2.2.2";
        in
        rec {
          mango-settings = pkgs.python3Packages.buildPythonApplication {
            pname = "mango-settings";
            inherit version;
            format = "other";
            src = self;
            propagatedBuildInputs = with pkgs; [
              python3Packages.pygobject3
              gtk3
            ];
            nativeBuildInputs = with pkgs; [ wrapGAppsHook ];
            dontWrapPythonPrograms = false;
            installPhase = ''
              runHook preInstall
              install -d $out/bin $out/share/mango-settings
              install -m 755 mango-settings.py $out/share/mango-settings/
              install -m 644 data/io.github.code_smith404.MangoSettings.desktop \
                $out/share/applications/ 2>/dev/null || true
              install -m 644 data/io.github.code_smith404.MangoSettings.svg \
                $out/share/icons/hicolor/scalable/apps/ 2>/dev/null || true
              makeWrapper $out/share/mango-settings/mango-settings.py $out/bin/mango-settings
              runHook postInstall
            '';
            meta = with pkgs.lib; {
              description = "Settings manager for the MangoWM Wayland compositor";
              homepage = "https://github.com/code-smith404/MangoWM-Settings";
              license = licenses.mit;
              platforms = platforms.linux;
            };
          };
          default = mango-settings;
        });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.system}.default}/bin/mango-settings";
        };
      });
    };
}
