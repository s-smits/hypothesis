# pr7/arm-F-bb7cf40

Runs: 2. Kind: live. Commit: `bb7cf40` (`bb7cf40a9f379a941c2defb6d52d899ac37db9d0`). Fork PR: 7. Lineage: 11 (integration of local patches; rebuilt later as #9/#11/#12 commits).

Commit basis: superloop.md pass 2 (arm F bb7cf40); run_armF.sh; benchmark ledger manifest.commit.

Ancestry (git merge-base --is-ancestor, in the repo object store): commit in tip / tip in commit.

| tip | commit is an ancestor of tip | tip is an ancestor of commit |
| --- | --- | --- |
| 8 | False | True |
| 7-old-2687572 | False | True |
| 7-old-c1ed566 | False | True |
| 7 | False | True |
| 9 | False | False |
| main | False | False |
| 11 | False | False |
| 12 | False | False |

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 14535b56 | ostir | sonnet-5-5 | 2 | 189150 | achieved | achieved | 1 | ostir 'achieved' is the verifier's judgement of 'higher than the first sequence', not a lift over the best input; notes/superloop.md (02:40, 03:05 audit): every kept sequence scores the same as input 3 (max(kept)=max(inputs)); 12 of 22 kept differ from their source in the last codon (F2) |
| 5fec1ed4 | arfA-cai | sonnet-5-5 | 1 | 102853 | achieved | achieved | 1 | benchmark ledger benchmark-ledger/20261004T010815871640Z-attempt-c3ba696700829fe9.json: loop mean_gap_closed 1.0 |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

