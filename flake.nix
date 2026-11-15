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
              install -d $out/bin $out/share/mango-settings \
                $out/share/applications \
                $out/share/icons/hicolor/scalable/apps \
                $out/share/man/man1 $out/share/metainfo \
                $out/share/licenses/mango-settings
              install -m 755 mango-settings.py $out/share/mango-settings/
              install -m 644 data/io.github.code_smith404.MangoSettings.desktop \
                $out/share/applications/
              install -m 644 data/io.github.code_smith404.MangoSettings.svg \
                $out/share/icons/hicolor/scalable/apps/
              install -m 644 data/mango-settings.1 $out/share/man/man1/
              install -m 644 data/io.github.code_smith404.MangoSettings.metainfo.xml \
                $out/share/metainfo/
              install -m 644 LICENSE $out/share/licenses/mango-settings/
              makeWrapper $out/share/mango-settings/mango-settings.py $out/bin/mango-settings
              runHook postInstall
            '';
            meta = with pkgs.lib; {
              description = "Settings manager for the MangoWM Wayland compositor";
              homepage = "https://github.com/code-smith404/MangoWM-Settings";
              license = licenses.mit;
              platforms = platforms.linux;
              mainProgram = "mango-settings";
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
