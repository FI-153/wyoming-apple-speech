.DEFAULT_GOAL := help

VENV_DIR := .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_PIP := $(VENV_DIR)/bin/pip
SWIFT_BIN := swift/.build/release/apple-stt
PORT ?= 10300
LANGUAGE ?= en

.PHONY: help venv build test quality run run-clean install uninstall clean

help: ## Show this help message
	@echo "Wyoming Apple STT"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Configuration:"
	@echo "  PORT=$(PORT)  LANGUAGE=$(LANGUAGE)"

venv: $(VENV_DIR)/bin/activate ## Create Python venv and install dev dependencies

$(VENV_DIR)/bin/activate: requirements-dev.txt requirements.txt
	python3 -m venv $(VENV_DIR)
	$(VENV_PIP) install --quiet -r requirements-dev.txt
	touch $(VENV_DIR)/bin/activate

build: $(SWIFT_BIN) ## Build the Swift CLI binary

$(SWIFT_BIN): swift/Package.swift swift/Sources/AppleSTT/*.swift
	cd swift && swift build -c release

test: venv ## Run Python tests
	$(VENV_PYTHON) -m pytest tests/ -v

quality: venv ## Run ruff linter and mypy type checker
	$(VENV_DIR)/bin/ruff check wyoming_apple_stt/ tests/
	$(VENV_DIR)/bin/mypy wyoming_apple_stt/

run-clean: venv ## Rebuild Swift binary and start server
	rm -f $(SWIFT_BIN)
	cd swift && swift build -c release
	$(VENV_PYTHON) -m wyoming_apple_stt \
		--uri tcp://0.0.0.0:$(PORT) \
		--apple-stt-bin $(SWIFT_BIN) \
		--language $(LANGUAGE) \
		--debug

run: venv build ## Run the server locally (Ctrl+C to stop)
	$(VENV_PYTHON) -m wyoming_apple_stt \
		--uri tcp://0.0.0.0:$(PORT) \
		--apple-stt-bin $(SWIFT_BIN) \
		--language $(LANGUAGE) \
		--debug

install: build ## Install as launchd service (PORT=10300 LANGUAGE=en)
	./scripts/install.sh $(PORT) $(LANGUAGE)

uninstall: ## Uninstall the launchd service and remove files
	./scripts/uninstall.sh

clean: ## Remove build artifacts and venv
	rm -rf $(VENV_DIR)
	rm -rf swift/.build
