# Prepared packs (never run)

`replication/` and `codonpair/` hold what was prepared for the operator to run: goals, a lane script, a scorer builder and scoring scripts. No live run was made from either pack, and the packs' scoring oracle (`scorer.json`) is not archived.

- `replication/run_armG.sh` is a PREPARED script and was never run. It is not the script of the live arm G: that arm ran `../pr12/arm-G-84dd787/scripts/run_armG_ran.sh` (ceiling 1,000,000, cap 500,000, port 7239, 8 rounds). Both are named run_armG; do not mix them up.
- In `replication/run_armG.sh` the MAX_ROUNDS default is 8 (the operator's cap of about 8 rounds); the copy in the notes had 20. Nothing else was changed; CAP stays 1,000,000.
- `replication/goals/goal_aroE.json` is byte-identical to the goal the live arm G ran for aroE. `../goals/goal_tusE.json` is the CAI goal the live arm G ran for tusE; `codonpair/goal_tusE.json` is a different, codon-pair goal.
- `codonpair/run_armH.sh` was prepared and not run either (it begins with `exit 1`).
- Python files are stored as `*.py.txt`: copy and drop the `.txt` to run.
