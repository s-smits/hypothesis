#!/bin/zsh
# Arm G (P8): #12 head 84dd787, Sonnet 5.5 builds and verifies, two runs AT ONCE on benchmark dev genes tusE and aroE (not run before),
# 8 rounds each (operator's word via the Audit Agent), a guard cap of 500,000 tokens per run; the operator said at 08:4x BST: renewed .env, launch two runs.
# Ceiling 1,000,000 = 2 x cap, reserved at the cap. Port 7239 is arm G's own; ports 7233/7234/8000/8001 are not mine.
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
CEILING=1000000; CAP=500000; PORT=7239; ARM=$S/arm-G; ROUNDS=8
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$ARM/results
if [ $((2 * CAP)) -gt $CEILING ]; then echo "$(date '+%F %T') arm G not opened: 2 x cap $((2*CAP)) above ceiling $CEILING" >> $S/arm_G.status; exit 1; fi
echo "$(date '+%F %T') arm G opens 2 runs at once, cap $CAP each, ceiling $CEILING, max rounds $ROUNDS" >> $S/arm_G.status
cd $ARM
for name in tusE aroE; do
  goal=$S/bench-pack/goal_$name.json
  (timeout 7200 uv run python -m temporal.run_hypothesis $goal \
     --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-sonnet-5-5 --max-rounds $ROUNDS --address localhost:$PORT > $S/run_G$name.log 2>&1
   echo "$(date '+%F %T') arm G $name ended rc=$?" >> $S/arm_G.status) &
done
(sleep 40; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name G --budget $CAP >> $S/watch_G.out 2>&1) &
sleep 30
uv run python $S/guard_armF.py $ARM/results $S/arm_G.status localhost:$PORT $CAP $CEILING >> $S/guard_G.out 2>&1
wait
echo "$(date '+%F %T') arm G lane finished" >> $S/arm_G.status
