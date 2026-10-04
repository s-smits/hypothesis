#!/bin/bash
# Score every saved run of an arm against ALL development genes. From recorded bytes only: no model, no
# network, no Temporal, no held-out gene. Prints one row per dev gene (a gene that never ran, or did not end
# achieved, counts as not achieved) and writes scores.txt, scores.json and one bench_score log per run.
#
# Usage: score_all.sh [results dir]        default: <this dir>/arm-G/results
# Env:   ARM  code that holds node_dag and temporal (default: the merged checkout; only read)
#        OUT  where scores go (default: <results dir>/../scores). Keep it OUTSIDE the results dir a run reads:
#             the logs name the exact optimum.
HERE="$(cd "$(dirname "$0")" && pwd)"
ARM="${ARM:-/Users/air/Developer/hypothesis-loop-merged}"
PY="${PY:-$ARM/.venv/bin/python}"
RESULTS="${1:-$HERE/arm-G/results}"
OUT="${OUT:-$(dirname "$RESULTS")/scores}"
if [ ! -d "$RESULTS/hypotheses" ]; then
  echo "no saved runs under $RESULTS/hypotheses: every dev gene counts as not achieved (never ran)" >&2
fi
mkdir -p "$RESULTS/hypotheses" "$OUT"
export PYTHONPATH="$ARM:$ARM/src" PYTHONDONTWRITEBYTECODE=1
exec "$PY" "$HERE/score_all.py" "$RESULTS" "$HERE/pack.json" "$HERE/bench_score.py" "$OUT"
