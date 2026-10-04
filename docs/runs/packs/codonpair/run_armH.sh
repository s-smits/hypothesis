#!/bin/zsh
# not approved: needs the operator's word on spend (the driver deletes this comment and the `exit 1` below it, and only then)
exit 1
# Arm H: the codon-pair objective on two benchmark dev genes (tusE, uspD), two runs AT ONCE, modelled on run_armF2.sh.
# Sonnet 5.5 builds and verifies, 8 rounds at most, a guard cap of 500,000 tokens per run, goal files from codonpair-pack/.
# THIS SPENDS TOKENS (it calls Anthropic through temporal.run_hypothesis). Nothing here has been run.
#
# Usage:  zsh run_armH.sh <ceiling tokens>        (or CEILING=<tokens> zsh run_armH.sh)
# Env:    ARM=<code dir>   a prepared checkout with its own .venv, like arm-F2 (default $S/arm-H); results go to $ARM/results
#         PORT=7239        the Temporal dev server port. A NEW one: 7233-7238 and 8000-8002 are refused. Start the dev server and
#                          a worker on it first, with NODE_DAG_RESULTS=$ARM/results, as for F2 (see dev_server.py). Nothing here starts them.
# Reserved spend is the guard's: settled runs plus every open run at its cap. It is a reservation, NOT a hard limit: the loop
# stops opening rounds above HypothesisInput.max_tokens (500,000, no CLI option) and never cuts a round in flight, so a run
# ends at about 500,000 plus the round that was running. With the pair table a round may cost 300,000 to 500,000 (see the report).
S=/private/tmp/claude-501/-Users-air-Developer-hypothesis/95f44646-4f13-48e9-a640-91afd80574df/scratchpad
SL=/Users/air/Developer/hypothesis/.claude/skills/super-loop/scripts/superloop.py
PACK=$S/codonpair-pack
CEILING=${1:-${CEILING:-}}; CAP=500000; ROUNDS=8; ROUND_WORST=${ROUND_WORST:-500000}
PORT=${PORT:-7239}; ARM=${ARM:-$S/arm-H}
STATUS=$S/arm_H.status
refuse() { echo "$(date '+%F %T') arm H not opened: $1" >> $STATUS; echo "arm H not opened: $1" >&2; exit 1; }
case "$CEILING" in ''|*[!0-9]*) refuse "give the ceiling in tokens as the first argument or CEILING (a whole number)";; esac
case "$PORT" in ''|*[!0-9]*) refuse "PORT must be a number";; 723[3-8]|800[0-2]) refuse "port $PORT is one of the ports other arms use";; esac
[ $((2 * CAP)) -gt $CEILING ] && refuse "2 x cap $((2*CAP)) above ceiling $CEILING"
[ -d "$ARM" ] || refuse "arm directory $ARM does not exist (prepare it like arm-F2)"
for name in tusE uspD; do [ -f $PACK/goal_$name.json ] || refuse "goal file $PACK/goal_$name.json is missing"; done
nc -z localhost $PORT 2>/dev/null || refuse "no Temporal server listens on localhost:$PORT (start the dev server and a worker first)"
set -a; . /Users/air/Developer/hypothesis/.env; set +a
export NODE_DAG_RESULTS=$ARM/results
echo "$(date '+%F %T') arm H opens 2 runs at once (tusE, uspD), cap $CAP each, ceiling $CEILING, max rounds $ROUNDS, port $PORT, arm $ARM" >> $STATUS
WORST=$((2 * (CAP + ROUND_WORST)))
[ $CEILING -lt $WORST ] && echo "$(date '+%F %T') note: ceiling $CEILING is below $WORST = 2 x (cap + one worst round of $ROUND_WORST); the guard reserves cap per open run, so real spend can pass the ceiling" >> $STATUS
cd $ARM
for name in tusE uspD; do
  (timeout 7200 uv run python -m temporal.run_hypothesis $PACK/goal_$name.json \
     --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-sonnet-5-5 --max-rounds $ROUNDS --address localhost:$PORT > $S/run_H$name.log 2>&1
   echo "$(date '+%F %T') arm H $name ended rc=$?" >> $STATUS) &
done
(sleep 40; cd /Users/air/Developer/hypothesis-loop-merged && python3 $SL watch start --name H --budget $CAP >> $S/watch_H.out 2>&1) &
sleep 30
uv run python $S/guard_armF.py $ARM/results $STATUS localhost:$PORT $CAP $CEILING >> $S/guard_H.out 2>&1
wait
echo "$(date '+%F %T') arm H lane finished" >> $STATUS
