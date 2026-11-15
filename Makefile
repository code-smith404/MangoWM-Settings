PREFIX ?= $(HOME)/.local
DESTDIR ?=
BIN_DIR = $(DESTDIR)$(PREFIX)/bin
SHARE_DIR = $(DESTDIR)$(PREFIX)/share/mango-settings
APP_DIR = $(DESTDIR)$(PREFIX)/share/applications
ICON_DIR = $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps
MAN_DIR = $(DESTDIR)$(PREFIX)/share/man/man1
METAINFO_DIR = $(DESTDIR)$(PREFIX)/share/metainfo
DESKTOP_ID = io.github.code_smith404.MangoSettings

.PHONY: all install uninstall test check-versions clean

all: test

install:
	install -d "$(BIN_DIR)" "$(SHARE_DIR)" "$(APP_DIR)" "$(ICON_DIR)" \
	    "$(MAN_DIR)" "$(METAINFO_DIR)"
	install -m 755 mango-settings.py "$(SHARE_DIR)/mango-settings.py"
	printf '#!/bin/sh\nbindir=$$(CDPATH= cd -- "$$(dirname -- "$$0")" && pwd)\nexec python3 "$$bindir/../share/mango-settings/mango-settings.py" "$$@"\n' > "$(BIN_DIR)/mango-settings"
	chmod +x "$(BIN_DIR)/mango-settings"
	install -m 644 data/$(DESKTOP_ID).svg "$(ICON_DIR)/"
	install -m 644 data/mango-settings.1 "$(MAN_DIR)/"
	install -m 644 data/$(DESKTOP_ID).metainfo.xml "$(METAINFO_DIR)/"
	# Generate the desktop entry with absolute Exec/TryExec paths: launchers
	# like rofi drop entries whose TryExec is not on *their* PATH, and
	# compositor sessions often do not include ~/.local/bin in PATH.
	sed -e "s|^Exec=.*|Exec=$(PREFIX)/bin/mango-settings|" \
	    -e "s|^TryExec=.*|TryExec=$(PREFIX)/bin/mango-settings|" \
	    data/$(DESKTOP_ID).desktop > "$(APP_DIR)/$(DESKTOP_ID).desktop"
	@if [ -z "$(DESTDIR)" ] && command -v update-desktop-database >/dev/null 2>&1; then \
		update-desktop-database "$(APP_DIR)" || true; \
	fi
	@if [ -z "$(DESTDIR)" ] && command -v appstreamcli >/dev/null 2>&1; then \
		appstreamcli validate --no-net "$(METAINFO_DIR)/$(DESKTOP_ID).metainfo.xml" || true; \
	fi

uninstall:
	rm -f "$(BIN_DIR)/mango-settings" "$(APP_DIR)/$(DESKTOP_ID).desktop"
	rm -f "$(ICON_DIR)/$(DESKTOP_ID).svg"
	rm -f "$(MAN_DIR)/mango-settings.1"
	rm -f "$(METAINFO_DIR)/$(DESKTOP_ID).metainfo.xml"
	rm -rf "$(SHARE_DIR)"

test:
	python3 -m unittest discover -s tests -v

check-versions:
	sh tests/check-versions.sh

clean:
	rm -rf __pycache__ tests/__pycache__
