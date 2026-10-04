#!/bin/zsh
# One arm: three runs in a row, each opened only if the token ceiling allows it.
ARM=$1; PORT=$2
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
CEILING=2000000; ESTIMATE=350000
OTHER=$([ "$ARM" = A ] && echo B || echo A)
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$S/arm-$ARM/results
for i in 1 2 3; do
  spent=$(python3 $S/spent.py $S)
  need=$ESTIMATE; [ -e $S/inflight_$OTHER ] && need=$((need + ESTIMATE))
  if [ $((spent + need)) -gt $CEILING ]; then
    echo "$(date '+%F %T') arm $ARM run $i not opened: spent $spent, need $need, ceiling $CEILING" >> $S/arm_$ARM.status
    break
  fi
  touch $S/inflight_$ARM
  echo "$(date '+%F %T') arm $ARM run $i opened (spent so far $spent)" >> $S/arm_$ARM.status
  (sleep 25; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name $ARM$i --budget 500000 >> $S/watch_$ARM$i.out 2>&1) &
  (cd $S/arm-$ARM && timeout 1800 uv run python -m temporal.run_hypothesis $S/arm_goal.json \
     --model anthropic:claude-sonnet-5-5 --max-rounds 3 --address localhost:$PORT > $S/run_$ARM$i.log 2>&1)
  rc=$?
  rm -f $S/inflight_$ARM
  echo "$(date '+%F %T') arm $ARM run $i ended rc=$rc" >> $S/arm_$ARM.status
done
echo "$(date '+%F %T') arm $ARM lane finished" >> $S/arm_$ARM.status
