{ lib
, stdenvNoCC
, python3
, gtk3
, glib
, gobject-introspection
, wrapGAppsHook3
}:

let
  pythonEnv = python3.withPackages (ps: [ ps.pygobject3 ]);
  version = "1.1.1";  # keep in sync with __version__ in mango-settings.py
in
stdenvNoCC.mkDerivation {
  pname = "mango-settings";
  inherit version;
  src = lib.cleanSource ../..;

  nativeBuildInputs = [ gobject-introspection wrapGAppsHook3 ];
  buildInputs = [ gtk3 pythonEnv ];

  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    ${pythonEnv}/bin/python3 -m unittest discover tests
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm755 mango-settings.py $out/bin/mango-settings
    install -Dm644 data/io.github.code_smith404.MangoSettings.desktop \
      $out/share/applications/io.github.code_smith404.MangoSettings.desktop
    install -Dm644 data/io.github.code_smith404.MangoSettings.svg \
      $out/share/icons/hicolor/scalable/apps/io.github.code_smith404.MangoSettings.svg
    runHook postInstall
  '';

  # gsettings for the Theming page
  preFixup = ''
    gappsWrapperArgs+=(--prefix PATH : ${lib.makeBinPath [ glib ]})
  '';

  meta = {
    description = "GNOME-style settings app for the MangoWM Wayland compositor";
    homepage = "https://github.com/code-smith404/MangoWM-Settings";
    license = lib.licenses.mit;
    mainProgram = "mango-settings";
    platforms = lib.platforms.linux;
  };
}
