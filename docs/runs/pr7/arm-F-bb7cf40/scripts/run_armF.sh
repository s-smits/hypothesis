#!/bin/zsh
# Arm F: the integrated local head, Sonnet 5.5 builds and verifies, two runs AT ONCE (ostir goal, benchmark gene arfA),
# 20 rounds each, a cap of 1,000,000 tokens per run and 2,000,000 for the arm. Reserved spend counts every open run at its cap.
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
CEILING=2000000; CAP=1000000; PORT=7237; ARM=$S/arm-F
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$ARM/results
if [ $((2 * CAP)) -gt $CEILING ]; then echo "$(date '+%F %T') arm F not opened: 2 x cap $((2*CAP)) above ceiling $CEILING" >> $S/arm_F.status; exit 1; fi
echo "$(date '+%F %T') arm F opens 2 runs at once, cap $CAP each, ceiling $CEILING, max rounds 20" >> $S/arm_F.status
cd $ARM
for name in ostir arfA; do
  goal=$S/arm_goal.json; [ $name = arfA ] && goal=$S/bench-pack/goal_arfA.json
  (timeout 7200 uv run python -m temporal.run_hypothesis $goal \
     --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-sonnet-5-5 --max-rounds 20 --address localhost:$PORT > $S/run_F$name.log 2>&1
   echo "$(date '+%F %T') arm F $name ended rc=$?" >> $S/arm_F.status) &
done
(sleep 40; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name F --budget $CAP >> $S/watch_F.out 2>&1) &
sleep 30
uv run python $S/guard_armF.py $ARM/results $S/arm_F.status localhost:$PORT $CAP $CEILING >> $S/guard_F.out 2>&1
wait
echo "$(date '+%F %T') arm F lane finished" >> $S/arm_F.status
