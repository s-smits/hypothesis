MODEL ?= anthropic:claude-haiku-4-5
LOGS := results/logs

.PHONY: start stop restart logs

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
	@nohup uv run python -m temporal.run_ui --model $(MODEL) > $(LOGS)/ui.log 2>&1 &
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
