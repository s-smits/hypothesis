# pr7/arm-B-88a44b9

Runs: 3. Kind: live. Commit: `88a44b9` (`88a44b99dcb76e68cce25d37e4b2e5e8f13d3f5f`). Fork PR: 7. Lineage: 7 (+ local per-filter-facts commit, never pushed).

Commit basis: superloop.md pass 1 (arm B worktree 88a44b9, cut from c1ed566); run_arm.sh.

Ancestry (git merge-base --is-ancestor, in the repo object store): commit in tip / tip in commit.

| tip | commit is an ancestor of tip | tip is an ancestor of commit |
| --- | --- | --- |
| 8 | False | False |
| 7-old-2687572 | False | False |
| 7-old-c1ed566 | False | True |
| 7 | False | False |
| 9 | False | False |
| main | False | False |
| 11 | False | False |
| 12 | False | False |

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2adaf0fd | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 1 | 83625 | achieved | achieved | 3 | ostir 'achieved' is the verifier's judgement of 'higher than the first sequence', not a lift over the best input; notes/superloop.md 22:50 BST: false accept (threshold 40.001 under baseline 41.6249; 2 of 49 kept only tie it) |
| ad7f1624 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 2 | 230394 | achieved | achieved | 3 | ostir 'achieved' is the verifier's judgement of 'higher than the first sequence', not a lift over the best input |
| f065baab | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 2 | 240931 | achieved | achieved | 3 | ostir 'achieved' is the verifier's judgement of 'higher than the first sequence', not a lift over the best input; notes/superloop.md 22:50 BST: false accept (threshold 40.001 under baseline 41.6249; 2 of 49 kept only tie it) |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

