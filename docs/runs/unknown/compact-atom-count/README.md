# unknown/compact-atom-count

Runs: 5. Kind: live. Commit: `unknown`. Fork PR: unknown. Lineage: 7 (earlier development of hypothesis-loop-compact).

Commit basis: no commit recorded: oracle REVIEW.md says 'The ledger records no commit'; reflog of the compact worktree shows HEAD 810fd84 from 18:25:11 to 19:30:25 BST (runs started 18:39-19:24 BST) but the worker's checkout is not recorded.

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| f8529510 | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 221358 | achieved | achieved | 5 | goal uses dna_atom_score, a node since removed: the saved files no longer load into the current code (oracle REVIEW.md) |
| f1bbba3e | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 222822 | achieved | achieved | 5 | goal uses dna_atom_score, a node since removed: the saved files no longer load into the current code (oracle REVIEW.md) |
| 6b82eb8e | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 238310 | not achieved | not achieved | 5 | goal uses dna_atom_score, a node since removed: the saved files no longer load into the current code (oracle REVIEW.md) |
| bbe2cc75 | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 183363 | not achieved | not achieved | 5 | goal uses dna_atom_score, a node since removed: the saved files no longer load into the current code (oracle REVIEW.md) |
| 5bf5f3e1 | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | 3 | 201149 | not achieved | not achieved | 5 | goal uses dna_atom_score, a node since removed: the saved files no longer load into the current code (oracle REVIEW.md) |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

