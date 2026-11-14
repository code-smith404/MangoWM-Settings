PREFIX ?= $(HOME)/.local
DESTDIR ?=
BIN_DIR = $(DESTDIR)$(PREFIX)/bin
SHARE_DIR = $(DESTDIR)$(PREFIX)/share/mango-settings
APP_DIR = $(DESTDIR)$(PREFIX)/share/applications
ICON_DIR = $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps
DESKTOP_ID = io.github.code_smith404.MangoSettings

.PHONY: all install uninstall test check-versions clean

all: test

install:
	install -d "$(BIN_DIR)" "$(SHARE_DIR)" "$(APP_DIR)" "$(ICON_DIR)"
	install -m 755 mango-settings.py "$(SHARE_DIR)/mango-settings.py"
	printf '#!/bin/sh\nexec python3 "%s/mango-settings.py" "$$@"\n' "$(PREFIX)/share/mango-settings" > "$(BIN_DIR)/mango-settings"
	chmod +x "$(BIN_DIR)/mango-settings"
	install -m 644 data/$(DESKTOP_ID).desktop "$(APP_DIR)/"
	install -m 644 data/$(DESKTOP_ID).svg "$(ICON_DIR)/"

uninstall:
	rm -f "$(BIN_DIR)/mango-settings" "$(APP_DIR)/$(DESKTOP_ID).desktop"
	rm -f "$(ICON_DIR)/$(DESKTOP_ID).svg"
	rm -rf "$(SHARE_DIR)"

test:
	python3 -m unittest discover -s tests -v

check-versions:
	sh tests/check-versions.sh

clean:
	rm -rf __pycache__ tests/__pycache__
