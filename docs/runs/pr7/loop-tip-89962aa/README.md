# pr7/loop-tip-89962aa

Runs: 1. Kind: live. Commit: `89962aa` (`89962aa1105cbec88f27de37f4a511fc08e0e43b`). Fork PR: 7. Lineage: 7.

Commit basis: reflog of worktree loop-tip: HEAD 89962aa from 21:52:42 BST; run started 21:52:55 BST.

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
| bead95ef | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 297109 | not achieved | not achieved | 1 | notes/superloop.md: r2 and r3 held 2/2 and kept 25 sequences; Haiku vetoed saying 41.625 < 41.6249 |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

