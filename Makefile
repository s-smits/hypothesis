MODEL ?=
LOGS := results/logs
# `make check` uses this venv; point it at another with `make check VENV=/path/to/venv`.
VENV ?= .venv
BIN := $(VENV)/bin
# What ruff already reported on 84dd787, one normalised line each (no line numbers).
RUFF_BASELINE := ruff-baseline.txt

.PHONY: start stop restart logs check test lint types

# Start the Temporal dev server (if not already up), the worker and the UI.
# The server keeps its history in results/temporal.db, so a restart keeps old runs.
start:
	@mkdir -p $(LOGS)
	@if ! pgrep -f "temporal server start-dev" > /dev/null; then \
		nohup temporal server start-dev --db-filename results/temporal.db \
			> $(LOGS)/temporal.log 2>&1 & \
		echo "Started Temporal server"; sleep 3; \
	else \
		echo "Temporal server already running"; \
	fi
	@nohup uv run python -m temporal.run_worker > $(LOGS)/worker.log 2>&1 &
	@nohup uv run python -m temporal.run_ui $(if $(MODEL),--model $(MODEL)) > $(LOGS)/ui.log 2>&1 &
	@echo "Started worker and UI (logs in $(LOGS)/)"
	@echo "  UI:          http://127.0.0.1:8000"
	@echo "  Temporal UI: http://localhost:8233"

# Stop the worker, the UI and the Temporal server.
stop:
	@-pkill -f "temporal.run_worker" && echo "Stopped worker" || true
	@-pkill -f "temporal.run_ui" && echo "Stopped UI" || true
	@-pkill -f "temporal server start-dev" && echo "Stopped Temporal server" || true

restart: stop
	@sleep 1
	@$(MAKE) --no-print-directory start

logs:
	@tail -f $(LOGS)/worker.log $(LOGS)/ui.log

# Tests, lint and types. Fails on a test failure, a type error, or a ruff finding
# or unformatted file that is not in $(RUFF_BASELINE).
check: test lint types

test:
	$(BIN)/python -m pytest -q

# Ruff has no baseline of its own: compare its findings (line numbers dropped, repeats
# kept) with $(RUFF_BASELINE), so only a new finding fails.
lint:
	@chk=$$($(BIN)/ruff check --output-format concise .); [ $$? -le 1 ] || exit 1; \
	fmt=$$($(BIN)/ruff format --check --output-format concise .); [ $$? -le 1 ] || exit 1; \
	cur=$$(printf '%s\n%s\n' "$$chk" "$$fmt" | grep -E '^[^ :]+:[0-9]+:[0-9]+: ' \
		| sed -E 's/^([^:]+):[0-9]+:[0-9]+:/\1:/' | LC_ALL=C sort); \
	new=$$(printf '%s\n' "$$cur" | LC_ALL=C comm -13 $(RUFF_BASELINE) -) || exit 1; \
	gone=$$(printf '%s\n' "$$cur" | LC_ALL=C comm -23 $(RUFF_BASELINE) -) || exit 1; \
	if [ -n "$$gone" ]; then echo "Fixed, so remove from $(RUFF_BASELINE):"; echo "$$gone"; fi; \
	if [ -n "$$new" ]; then echo "New ruff findings (not in $(RUFF_BASELINE)):"; echo "$$new"; exit 1; fi; \
	echo "ruff: nothing new"

types:
	$(BIN)/ty check --python $(VENV)
