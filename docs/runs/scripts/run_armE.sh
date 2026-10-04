#!/bin/zsh
# Arm E: 74803ee, Sonnet 5.5 builds and verifies. One run on the ostir goal, one on benchmark gene arfA.
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
CEILING=2000000; ESTIMATE=350000; PORT=7236
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$S/arm-E/results
for name in ostir arfA; do
  goal=$S/arm_goal.json; [ $name = arfA ] && goal=$S/bench-pack/goal_arfA.json
  spent=$(python3 $S/spent_pass2.py)
  if [ $((spent + ESTIMATE)) -gt $CEILING ]; then
    echo "$(date '+%F %T') arm E $name not opened: spent $spent, ceiling $CEILING" >> $S/arm_E.status; continue
  fi
  echo "$(date '+%F %T') arm E $name opened (spent so far $spent)" >> $S/arm_E.status
  (sleep 25; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name E$name --budget 700000 >> $S/watch_E$name.out 2>&1) &
  (cd $S/arm-E && timeout 2400 uv run python -m temporal.run_hypothesis $goal \
     --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-sonnet-5-5 --max-rounds 3 --address localhost:$PORT > $S/run_E$name.log 2>&1)
  echo "$(date '+%F %T') arm E $name ended rc=$?" >> $S/arm_E.status
done
echo "$(date '+%F %T') arm E lane finished" >> $S/arm_E.status
