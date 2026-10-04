#!/bin/zsh
# Arm F2: the integrated local head 07f1f87, Sonnet 5.5 builds and verifies, two runs AT ONCE (ostir goal, benchmark gene arfA),
# 8 rounds each (the operator's word relayed by the Audit Agent: stop after about 8), a guard cap of 700,000 tokens per run.
# Arm F's ceiling was 2,000,000 with 292,003 spent: 1,707,997 remain, and 2 x 700,000 reserved fits under it.
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
CEILING=1707997; CAP=700000; PORT=7238; ARM=$S/arm-F2; ROUNDS=8
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$ARM/results
if [ $((2 * CAP)) -gt $CEILING ]; then echo "$(date '+%F %T') arm F2 not opened: 2 x cap $((2*CAP)) above ceiling $CEILING" >> $S/arm_F2.status; exit 1; fi
echo "$(date '+%F %T') arm F2 opens 2 runs at once, cap $CAP each, ceiling $CEILING, max rounds $ROUNDS" >> $S/arm_F2.status
cd $ARM
for name in ostir arfA; do
  goal=$S/arm_goal.json; [ $name = arfA ] && goal=$S/bench-pack/goal_arfA.json
  (timeout 7200 uv run python -m temporal.run_hypothesis $goal \
     --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-sonnet-5-5 --max-rounds $ROUNDS --address localhost:$PORT > $S/run_F2$name.log 2>&1
   echo "$(date '+%F %T') arm F2 $name ended rc=$?" >> $S/arm_F2.status) &
done
(sleep 40; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name F2 --budget $CAP >> $S/watch_F2.out 2>&1) &
sleep 30
uv run python $S/guard_armF.py $ARM/results $S/arm_F2.status localhost:$PORT $CAP $CEILING >> $S/guard_F2.out 2>&1
wait
echo "$(date '+%F %T') arm F2 lane finished" >> $S/arm_F2.status
