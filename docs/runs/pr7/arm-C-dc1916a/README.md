# pr7/arm-C-dc1916a

Runs: 2. Kind: live. Commit: `dc1916a` (`dc1916a635d265bb05c81a36ef0e598e284447f7`). Fork PR: 7. Lineage: 7 (includes #8 benchmark harness).

Commit basis: superloop.md pass 2 (arm C = dc1916a); run_armC.sh.

Ancestry (git merge-base --is-ancestor, in the repo object store): commit in tip / tip in commit.

| tip | commit is an ancestor of tip | tip is an ancestor of commit |
| --- | --- | --- |
| 8 | False | True |
| 7-old-2687572 | False | True |
| 7-old-c1ed566 | False | True |
| 7 | True | True |
| 9 | True | False |
| main | True | False |
| 11 | True | False |
| 12 | True | False |

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a9d91571 | ostir | sonnet-5-5 | 3 | 403297 | abandoned | abandoned | 2 | - |
| e5917e38 | ostir | sonnet-5-5 | 3 | 431892 | not achieved | not achieved | 2 | - |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

