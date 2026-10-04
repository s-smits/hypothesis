# pr7/loop-tip-37e8de9

Runs: 1. Kind: live. Commit: `37e8de9` (`37e8de948f01edfeac520d741583b309b2c4adc7`). Fork PR: 7. Lineage: 7.

Commit basis: reflog of worktree loop-tip: HEAD 37e8de9 from 21:40:21 to 21:52:42 BST; run started 21:41:02 BST.

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
| 3bec2a48 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 313544 | abandoned | abandoned | 1 | - |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

