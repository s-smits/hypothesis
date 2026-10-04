# pre-pr7/compact-be3805f

Runs: 2. Kind: live. Commit: `be3805f` (`be3805f5a725a9d8f190987829b02701888624ae`). Fork PR: pre-7. Lineage: 7 (development commits of hypothesis-loop-compact).

Commit basis: handovers/oracle-20261003-loop-review.md table: 'be3805f the code every model call of this run executed'; reflog of hypothesis-loop-compact: be3805f HEAD from 19:45:43 BST, runs started 20:04 and 20:07 BST.

Ancestry (git merge-base --is-ancestor, in the repo object store): commit in tip / tip in commit.

| tip | commit is an ancestor of tip | tip is an ancestor of commit |
| --- | --- | --- |
| 8 | False | False |
| 7-old-2687572 | True | False |
| 7-old-c1ed566 | True | False |
| 7 | True | False |
| 9 | True | False |
| main | True | False |
| 11 | True | False |
| 12 | True | False |

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9afcd59e | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 2 | 170098 | abandoned | abandoned | 2 | - |
| d10361e4 | ostir | not in a ledger line | 5 | 445053 | blocked | - | 2 | blocked in round 5 on a node nobody wrote, 445,053 of its 500,000 tokens; no ledger line; run left open; saved state 'blocked' counted as no result (not achieved, not abandoned) |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

