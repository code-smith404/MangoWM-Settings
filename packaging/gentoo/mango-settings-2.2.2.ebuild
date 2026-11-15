# Copyright 1999-2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="GTK3 settings manager for the MangoWM Wayland compositor"
HOMEPAGE="https://github.com/code-smith404/MangoWM-Settings"
SRC_URI="https://github.com/code-smith404/MangoWM-Settings/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz"
S="${WORKDIR}/MangoWM-Settings-${PV}"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64 ~arm64 ~riscv"

RDEPEND="
	dev-python/pygobject:3
	dev-lang/python:3.11
	x11-libs/gtk+:3[introspection]
"
BDEPEND="sys-apps/coreutils"

src_compile() {
	python3 -m py_compile mango-settings.py || die
}

src_test() {
	python3 -m unittest discover -s tests -v || die
}

src_install() {
	emake DESTDIR="${D}" PREFIX=/usr install
	einstalldocs
}
