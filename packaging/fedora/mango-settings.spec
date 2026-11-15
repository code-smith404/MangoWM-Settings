%global debug_package %{nil}

Name:           mango-settings
Version:        2.2.2
Release:        1%{?dist}
Summary:        Settings manager for the MangoWM Wayland compositor

License:        MIT
URL:            https://github.com/code-smith404/MangoWM-Settings
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  make
BuildRequires:  python3
Requires:       python3
Requires:       python3-gobject
Requires:       gtk3
Recommends:     desktop-file-utils
Suggests:       mmsg

%description
Mango Settings is a GTK 3 configuration tool for the MangoWM Wayland
compositor. It edits MangoWM's configuration files in place while
preserving comments, ordering, symlinks and file permissions, and writes
changes atomically after creating timestamped backups.

It provides pages for appearance, animations, layouts, keybindings,
workspaces, window rules, monitors, input devices, startup apps, theming
and wallpapers, plus a keybinding editor with conflict detection and
headless configuration validation with --check.

%prep
%autosetup -n MangoWM-Settings-%{version}

%build
# Pure Python: nothing to compile.

%check
%{__python3} -m unittest discover -s tests -v
%{__python3} -m py_compile mango-settings.py
%{__python3} mango-settings.py --list-pages >/dev/null
HOME=%{buildroot}/tmp/home %{__python3} mango-settings.py --check || true

%install
make install DESTDIR=%{buildroot} PREFIX=/usr

%files
%doc README.md CHANGELOG.md
%license LICENSE
%{_bindir}/mango-settings
%{_datadir}/mango-settings/
%{_datadir}/applications/io.github.code_smith404.MangoSettings.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.code_smith404.MangoSettings.svg
%{_datadir}/metainfo/io.github.code_smith404.MangoSettings.metainfo.xml
%{_mandir}/man1/mango-settings.1*

%changelog
* Fri Nov 14 2025 Harikesh Verma <code-smith404@users.noreply.github.com> - 2.2.2-1
- Initial RPM packaging.
