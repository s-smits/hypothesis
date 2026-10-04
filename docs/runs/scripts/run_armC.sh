#!/bin/zsh
# Arm C: three runs in a row on PR #7's head, Sonnet 5.5 builds and verifies. Fresh ceiling for pass 2.
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
CEILING=2000000; ESTIMATE=350000; PORT=7235
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$S/arm-C/results
for i in 1 2 3; do
  spent=$(python3 - <<PY
import json, pathlib
f = pathlib.Path("$S/arm-C/results/ledger.jsonl")
t = 0
if f.exists():
    for l in f.read_text().splitlines():
        try: t += int(json.loads(l).get("tokens") or 0)
        except (ValueError, TypeError): pass
print(t)
PY
)
  if [ $((spent + ESTIMATE)) -gt $CEILING ]; then
    echo "$(date '+%F %T') arm C run $i not opened: spent $spent, ceiling $CEILING" >> $S/arm_C.status; break
  fi
  echo "$(date '+%F %T') arm C run $i opened (spent so far $spent)" >> $S/arm_C.status
  (sleep 25; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name C$i --budget 700000 >> $S/watch_C$i.out 2>&1) &
  (cd $S/arm-C && timeout 2400 uv run python -m temporal.run_hypothesis $S/arm_goal.json \
     --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-sonnet-5-5 --max-rounds 3 --address localhost:$PORT > $S/run_C$i.log 2>&1)
  echo "$(date '+%F %T') arm C run $i ended rc=$?" >> $S/arm_C.status
done
echo "$(date '+%F %T') arm C lane finished" >> $S/arm_C.status
