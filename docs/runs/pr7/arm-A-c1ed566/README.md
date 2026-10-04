# pr7/arm-A-c1ed566

Runs: 3. Kind: live. Commit: `c1ed566` (`c1ed566867a13c1693c524106c50c2c1dd7a6f67`). Fork PR: 7. Lineage: 7.

Commit basis: superloop.md pass 1 (arm A worktree c1ed566); run_arm.sh.

Ancestry (git merge-base --is-ancestor, in the repo object store): commit in tip / tip in commit.

| tip | commit is an ancestor of tip | tip is an ancestor of commit |
| --- | --- | --- |
| 8 | False | False |
| 7-old-2687572 | True | False |
| 7-old-c1ed566 | True | True |
| 7 | True | False |
| 9 | True | False |
| main | True | False |
| 11 | True | False |
| 12 | True | False |

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 26acc0f5 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 460705 | not achieved | not achieved | 3 | - |
| 45b928e8 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 369784 | achieved | achieved | 3 | ostir 'achieved' is the verifier's judgement of 'higher than the first sequence', not a lift over the best input; notes/superloop.md 22:50 BST: false accept (kept 104, 27 at or below baseline 88211.25) |
| 6dc8b1bd | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 358751 | not achieved | not achieved | 3 | - |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

