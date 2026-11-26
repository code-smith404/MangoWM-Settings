#
# openSUSE / OBS spec file.
# Build locally with:  rpmbuild -ba packaging/opensuse/mango-settings.spec
# Or add the repository to OBS and let it build for openSUSE Tumbleweed/Leap.
#
%global debug_package %{nil}

Name:           mango-settings
Version:        2.4.0
Release:        0
Summary:        Settings manager for the MangoWM Wayland compositor

License:        MIT
Group:          System/GUI/Other
URL:            https://github.com/code-smith404/MangoWM-Settings
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  make
BuildRequires:  python3-base
%if 0%{?suse_version}
BuildRequires:  update-desktop-files
%endif
Requires:       python3
Requires:       python3-gobject
Requires:       gtk3
Recommends:     mmsg

%description
Mango Settings is a GTK 3 configuration tool for the MangoWM Wayland
compositor. It edits MangoWM's configuration files in place while
preserving comments, ordering, symlinks and file permissions, and writes
changes atomically after creating timestamped backups.

%prep
%setup -q -n MangoWM-Settings-%{version}

%build
# Pure Python: nothing to compile.

%check
python3 -m unittest discover -s tests -v

%install
make install DESTDIR=%{buildroot} PREFIX=/usr

%if 0%{?suse_version}
%suse_update_desktop_file -r io.github.code_smith404.MangoSettings Settings DesktopSettings
%endif

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
* Fri Nov 14 2025 Harikesh Verma <code-smith404@users.noreply.github.com> - 2.2.2-0
- Initial openSUSE packaging.
