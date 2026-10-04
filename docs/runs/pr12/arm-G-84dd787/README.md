# pr12/arm-G-84dd787

Runs: 2. Kind: live. Commit: `84dd787` (`84dd7876e2471fa80b72219d84c4eb55bd9e09c5`). Fork PR: 12. Lineage: 12 (display, top of the stack then).

Commit basis: worktree $S/arm-G: `git rev-parse HEAD` = 84dd787, clean; arm_G.status 'worktree arm-G at 84dd787'; run_armG.sh header '#12 head 84dd787'.

Ancestry (git merge-base --is-ancestor, in the repo object store): commit in tip / tip in commit.

| tip | commit is an ancestor of tip | tip is an ancestor of commit |
| --- | --- | --- |
| 8 | False | True |
| 7-old-2687572 | False | True |
| 7-old-c1ed566 | False | True |
| 7 | False | True |
| 9 | False | True |
| main | False | True |
| 11 | False | True |
| 12 | True | True |

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2070ae10 | aroE-cai | sonnet-5-5 | 1 | 122113 | achieved | achieved | 1 | 'achieved' is the loop's verdict; per sweep/armG.md the benchmark gate that honours immutable (PR #14) fails aroE on stop codon TGA to TAA (1 of 2 passes it), the PR #8 gate ignores immutable and passes both; not re-derived here; goal file: packs/replication/goals/goal_aroE.json (byte-identical); n=1 per gene; both kept exactly one sequence; no critique call (round 1 accepted) |
| a6fab082 | tusE-cai | sonnet-5-5 | 1 | 150699 | achieved | achieved | 1 | 'achieved' is the loop's verdict; per sweep/armG.md the benchmark gate that honours immutable (PR #14) fails aroE on stop codon TGA to TAA (1 of 2 passes it), the PR #8 gate ignores immutable and passes both; not re-derived here; goal file: goals/goal_tusE.json; n=1 per gene; both kept exactly one sequence; no critique call (round 1 accepted) |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

