#!/bin/bash
# Arm G: the benchmark's development genes through the HypothesisLoop, TWO RUNS AT ONCE from a queue.
# Modelled on run_armF.sh (Sonnet 5.5 builds and verifies, 20 rounds at most, 1,000,000 tokens per run), with
# the ceiling checked BEFORE EVERY OPEN by armG_lane.py: reserved spend = tokens of runs that ended + every
# open run at its cap (never settled rows only) + the cap of the run about to open.
#
# THIS SPENDS TOKENS: it calls Anthropic through temporal.run_hypothesis. Nothing here has been run.
#
# Usage:   run_armG.sh <ceiling tokens> [gene ...]        (no genes = all development genes, in dev_genes.tsv order)
# Example: run_armG.sh 6200000                            (all 9; see "ceiling" in replication-pack.md)
#          DRY_RUN=1 run_armG.sh 6200000                  (prints the plan, starts nothing, reads no .env)
# Env (all optional):
#   ARM=<code dir>        the checkout under test, only read (default: the merged checkout). For a clean arm make a
#                         worktree and pass it here.
#   ROOT=<out dir>        results, logs, status and scores go here (default: <this dir>/arm-G). Use a fresh one per arm.
#   PORT=7240             Temporal dev server this script starts (and stops). Pick a free port; it refuses a busy one.
#   START_INFRA=1         0 = use a server and worker you already started on PORT with NODE_DAG_RESULTS=$ROOT/results.
#   CAP=1000000  MAX_ROUNDS=8  TIMEOUT=7200 (seconds per run)  WATCH=1 (a read-only pulse look every 290 s)
#   TICK=20 (seconds between looks at the saved runs)  STAGGER=30 (seconds to wait after an open)
#   MODEL / VERIFY_MODEL  default anthropic:claude-sonnet-5-5 for both, as in arm F.
#   ENVFILE=<file>        sourced for the API key, as run_armF.sh did. Never printed.
HERE="$(cd "$(dirname "$0")" && pwd)"
CEILING="${1:-}"; shift
ARM="${ARM:-/Users/air/Developer/hypothesis-loop-merged}"
ROOT="${ROOT:-$HERE/arm-G}"
PORT="${PORT:-7240}"
CAP="${CAP:-1000000}"; CONC=2
# Operator cap: about 8 rounds (default lowered from 20).
MAX_ROUNDS="${MAX_ROUNDS:-8}"; TIMEOUT="${TIMEOUT:-7200}"
MODEL="${MODEL:-anthropic:claude-sonnet-5-5}"; VERIFY_MODEL="${VERIFY_MODEL:-anthropic:claude-sonnet-5-5}"
ENVFILE="${ENVFILE:-/Users/air/Developer/hypothesis/.env}"
PY="${PY:-$ARM/.venv/bin/python}"
SL="${SL:-/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py}"
# arfA and tusE goals already exist; they are reused as they are (this dir's goals/ is looked in first).
EXISTING_GOALS="${EXISTING_GOALS:-/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad/bench-pack}"
STATUS="$ROOT/arm_G.status"

case "$CEILING" in ''|*[!0-9]*) echo "usage: run_armG.sh <ceiling tokens, a whole number> [gene ...]" >&2; exit 2;; esac
if [ "$#" -gt 0 ]; then GENES=("$@"); else GENES=($(awk -F'\t' 'NR>1 {print $1}' "$HERE/dev_genes.tsv")); fi
N=${#GENES[@]}
RECOMMENDED=$(( CONC * CAP + (N > CONC ? N - CONC : 0) * 600000 ))

if [ $((CONC * CAP)) -gt "$CEILING" ]; then
  echo "arm G not opened: $CONC x cap $((CONC * CAP)) is above ceiling $CEILING" >&2; exit 1
fi
[ "$CEILING" -lt "$RECOMMENDED" ] && echo "note: ceiling $CEILING is below $RECOMMENDED, the recommended ceiling for $N genes; the queue may stop early (genes not opened are named)." >&2

echo "arm G plan: $N genes ${GENES[*]}"
echo "  ceiling $CEILING, cap $CAP per run, $CONC at once, max rounds $MAX_ROUNDS, wall limit ${TIMEOUT}s, models $MODEL / $VERIFY_MODEL"
echo "  code $ARM (read only), results $ROOT/results, Temporal port $PORT (start_infra=${START_INFRA:-1})"
missing=0
for g in "${GENES[@]}"; do
  if   [ -f "$HERE/goals/goal_$g.json" ];        then echo "  goal $g: $HERE/goals/goal_$g.json"
  elif [ -f "$EXISTING_GOALS/goal_$g.json" ];    then echo "  goal $g: $EXISTING_GOALS/goal_$g.json"
  else echo "  goal $g: NOT FOUND" >&2; missing=1; fi
done
[ "$missing" = 1 ] && { echo "a goal file is missing; nothing started" >&2; exit 1; }
[ -x "$PY" ] || { echo "no python at $PY (set ARM or PY)" >&2; exit 1; }
if [ "${DRY_RUN:-0}" = 1 ]; then echo "DRY_RUN=1: nothing started."; exit 0; fi

mkdir -p "$ROOT/results"
export PYTHONPATH="$ARM:$ARM/src" PYTHONDONTWRITEBYTECODE=1
[ -f "$ENVFILE" ] && { set -a; . "$ENVFILE"; set +a; }
export NODE_DAG_RESULTS="$ROOT/results"
echo "$(date '+%F %T') arm G starts: $N genes, ceiling $CEILING, cap $CAP, concurrency $CONC, port $PORT" >> "$STATUS"

PIDS=(); STARTED=0
cleanup() {
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
  # The dev server binary is a child of its python launcher and can outlive it. Only when this script started
  # the server (the port was free before) is whatever still listens on PORT ours to stop.
  if [ "$STARTED" = 1 ]; then lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | while read -r p; do kill "$p" 2>/dev/null; done; fi
}
trap cleanup EXIT
if [ "${START_INFRA:-1}" = 1 ]; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $PORT is in use; pick another with PORT=... (this script will not attach to someone else's server)" >&2; exit 1
  fi
  (cd "$ARM" && exec "$PY" "$HERE/dev_server_armG.py" "$PORT") > "$ROOT/dev_server.log" 2>&1 &
  PIDS+=($!); STARTED=1
  for _ in $(seq 1 60); do
    "$PY" -c "import socket,sys; socket.create_connection(('127.0.0.1', $PORT), 1).close()" 2>/dev/null && break
    sleep 1
  done
  (cd "$ARM" && exec "$PY" -m temporal.run_worker --address "localhost:$PORT") > "$ROOT/worker.log" 2>&1 &
  PIDS+=($!)
  sleep 10
fi
if [ "${WATCH:-1}" = 1 ] && [ -f "$SL" ]; then
  (sleep 40; cd "$ARM" && python3 "$SL" watch start --name G --budget "$CAP" >> "$ROOT/watch_G.out" 2>&1) &
fi

"$PY" "$HERE/armG_lane.py" --ceiling "$CEILING" --cap "$CAP" --concurrency "$CONC" \
  --results "$ROOT/results" --status "$STATUS" --address "localhost:$PORT" \
  --runner "$PY -m temporal.run_hypothesis" --cwd "$ARM" \
  --goal-dirs "$HERE/goals" "$EXISTING_GOALS" \
  --model "$MODEL" --verify-model "$VERIFY_MODEL" --max-rounds "$MAX_ROUNDS" --timeout "$TIMEOUT" \
  --tick "${TICK:-20}" --stagger "${STAGGER:-30}" \
  "${GENES[@]}"
RC=$?
echo "$(date '+%F %T') arm G lane finished rc=$RC" >> "$STATUS"
echo; echo "=== per-gene table (recorded bytes only) ==="
"$HERE/score_all.sh" "$ROOT/results"
exit $RC
