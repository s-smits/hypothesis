#!/bin/zsh
# Arm H: the #20 head 822d2ea (all sweep candidates merged on 46e125f), Sonnet 5.5 builds, verifies and critiques, two runs AT ONCE:
#   H1 = benchmark dev gene aroE with goal_for's fixed-codon criterion (#17), H2 = the ostir goal as in arm F2.
# 8 rounds each, a guard cap of 500,000 tokens per run, ceiling 1,000,000 = 2 x cap reserved at the cap. The operator authorised two runs
# on this stack at ~10:25 BST (the user's message: "launch 2 runs for forked PRs that are not battle-tested yet"). Port 7241 is arm H's own;
# ports 7233/7234/8000/8001 are not mine. Predictions P9 and P10 are frozen.
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
CEILING=1000000; CAP=500000; PORT=7241; ARM=$S/arm-H; ROUNDS=8
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$ARM/results
if [ $((2 * CAP)) -gt $CEILING ]; then echo "$(date '+%F %T') arm H not opened: 2 x cap above ceiling" >> $S/arm_H.status; exit 1; fi
cd $ARM
uv run python $S/dev_server.py $PORT > $S/dev_7241h.log 2>&1 &
echo "server $!" > $S/arm_H.pids
for i in $(seq 1 30); do grep -q "temporal dev server on" $S/dev_7241h.log 2>/dev/null && break; sleep 2; done
uv run python -m temporal.run_worker --address localhost:$PORT > $S/worker_H.log 2>&1 &
echo "worker $!" >> $S/arm_H.pids
sleep 10
echo "$(date '+%F %T') arm H opens 2 runs at once, cap $CAP each, ceiling $CEILING, max rounds $ROUNDS, code $(git rev-parse --short HEAD)" >> $S/arm_H.status
for name in H1 H2; do
  goal=$S/arm_goal.json; [ $name = H1 ] && goal=$S/bench-pack-H/goal_aroE_fixed.json
  (timeout 7200 uv run python -m temporal.run_hypothesis $goal \
     --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-sonnet-5-5 --max-rounds $ROUNDS --address localhost:$PORT > $S/run_$name.log 2>&1
   echo "$(date '+%F %T') arm H $name ended rc=$?" >> $S/arm_H.status) &
done
(sleep 40; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name H --budget $CAP >> $S/watch_H.out 2>&1) &
sleep 30
uv run python $S/guard_armF.py $ARM/results $S/arm_H.status localhost:$PORT $CAP $CEILING >> $S/guard_H.out 2>&1
wait
echo "$(date '+%F %T') arm H lane finished; stopping my own worker and server" >> $S/arm_H.status
for p in $(awk '{print $2}' $S/arm_H.pids); do pkill -P $p 2>/dev/null; kill $p 2>/dev/null; done
