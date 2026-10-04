# pr7/arm-F2-07f1f87

Runs: 2. Kind: live. Commit: `07f1f87` (`07f1f87e884a0b0c75c4c79d14558131d5c27ab4`). Fork PR: 7. Lineage: 11 (prompt tightening on F; rebuilt later as #11 commits).

Commit basis: superloop.md pass 2 (arm F2 07f1f87); run_armF2.sh; benchmark ledger manifest.commit.

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
| 894a1eba | arfA-cai | sonnet-5-5 | 1 | 144385 | achieved | achieved | 1 | benchmark ledger benchmark-ledger/20261004T012551221119Z-attempt-5c6ab310164a3ee3.json: loop mean_gap_closed 1.0; tokens are a lower bound: two 600 s API stalls, cancelled attempts' tokens are not counted (notes/superloop.md 03:05) |
| ce899c7c | ostir | sonnet-5-5 | 2 | 232256 | achieved | achieved | 1 | ostir 'achieved' is the verifier's judgement of 'higher than the first sequence', not a lift over the best input; notes/superloop.md (02:40, 03:05 audit): every kept sequence scores the same as input 3 (max(kept)=max(inputs)); 12 of 22 kept differ from their source in the last codon (F2) |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

