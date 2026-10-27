# Pro Space Optimizer - developer Makefile
#
# Common targets:
#   make            show this help
#   make run        run from source (no install)
#   make test       run the automated test suite
#   make deb        build a self-contained .deb (bundles Python + PySide6)
#   make install    install the built .deb (needs sudo)
#   make remove     remove the installed package (needs sudo)
#   make inspect    show metadata + file list of the built .deb
#   make clean      remove build artefacts and the .deb

APP_NAME := pro-space-optimizer
VERSION  := 1.0.0
REVISION := 1
ARCH     := amd64
DEB      := $(APP_NAME)_$(VERSION)-$(REVISION)_$(ARCH).deb

PYTHON ?= python3
DPKG_DEB := $(shell command -v dpkg-deb 2>/dev/null)

.DEFAULT_GOAL := help
.PHONY: help run test lint deb install remove inspect clean

help:
	@echo "Pro Space Optimizer - available targets"
	@echo ""
	@echo "  make run       Run the application from source"
	@echo "  make test      Run the test suite (pytest)"
	@echo "  make lint      Run ruff if available"
	@echo "  make deb       Build $(DEB) (self-contained bundle)"
	@echo "  make install   Install $(DEB) via apt (sudo)"
	@echo "  make remove    Remove the installed package (sudo)"
	@echo "  make inspect   Show $(DEB) metadata and contents"
	@echo "  make clean     Remove build/ and $(DEB)"
	@echo ""
	@echo "Build requirements: $(PYTHON), pip, venv, dpkg-deb"
	@echo "Packaging script:   packaging/deb/build-deb.sh"

run:
	$(PYTHON) main.py

test:
	QT_QPA_PLATFORM=offscreen $(PYTHON) -m pytest -q

lint:
	@command -v ruff >/dev/null 2>&1 && ruff check src main.py tests || \
		echo "ruff not installed; skipping"

deb:
	@command -v dpkg-deb >/dev/null 2>&1 || { \
		echo "dpkg-deb not found. Install it with: sudo apt install dpkg"; \
		exit 1; }
	bash packaging/deb/build-deb.sh

install: $(DEB)
	sudo apt install ./$(DEB)

remove:
	sudo apt remove $(APP_NAME)

inspect: $(DEB)
	dpkg-deb -I $(DEB)
	@echo "----------------------------------------"
	dpkg-deb -c $(DEB)

$(DEB):
	$(MAKE) deb

clean:
	rm -rf build
	rm -f $(APP_NAME)_*-*_$(ARCH).deb
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
