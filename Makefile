# Standard install for packagers:  make install PREFIX=/usr DESTDIR="$pkgdir"
PREFIX  ?= /usr/local
DESTDIR ?=
APP_ID  := io.github.code_smith404.MangoSettings
PYTHON  ?= python3

SHARE   := $(DESTDIR)$(PREFIX)/share

.PHONY: all install uninstall test lint

all:
	@echo "Nothing to build. Run 'make install' (PREFIX=$(PREFIX))."

install:
	install -Dm755 mango-settings.py $(SHARE)/mango-settings/mango-settings.py
	install -dm755 $(DESTDIR)$(PREFIX)/bin
	ln -sf ../share/mango-settings/mango-settings.py $(DESTDIR)$(PREFIX)/bin/mango-settings
	install -Dm644 data/$(APP_ID).desktop $(SHARE)/applications/$(APP_ID).desktop
	install -Dm644 data/$(APP_ID).svg $(SHARE)/icons/hicolor/scalable/apps/$(APP_ID).svg
	install -Dm644 README.md $(SHARE)/doc/mango-settings/README.md
	install -Dm644 LICENSE $(SHARE)/licenses/mango-settings/LICENSE

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/mango-settings
	rm -rf $(SHARE)/mango-settings $(SHARE)/doc/mango-settings $(SHARE)/licenses/mango-settings
	rm -f $(SHARE)/applications/$(APP_ID).desktop
	rm -f $(SHARE)/icons/hicolor/scalable/apps/$(APP_ID).svg

test:
	$(PYTHON) -m unittest discover -v tests

lint:
	$(PYTHON) -m pyflakes mango-settings.py tests
	shellcheck -s sh install.sh
