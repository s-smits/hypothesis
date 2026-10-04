# pr7/arm-E-74803ee

Runs: 2. Kind: live. Commit: `74803ee` (`74803ee22af0fa1148b82aa0a339c17a5df5e7da`). Fork PR: 7. Lineage: 11 (beats_reference filter; rebuilt later as #11 commits).

Commit basis: superloop.md pass 2 (change E 74803ee, arm E); run_armE.sh; benchmark ledger manifest.commit.

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
| 6148f02d | arfA-cai | sonnet-5-5 | 3 | 569667 | achieved | achieved | 1 | benchmark ledger benchmark-ledger/20261004T010815770703Z-attempt-a85da005fefebd11.json: loop mean_gap_closed 0.05069169372732716 |
| 88c26e22 | ostir | sonnet-5-5 | 3 | 437695 | not achieved | not achieved | 1 | - |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (each round's executed DAG), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `ledger.jsonl` (the loop's ledger lines for these runs, unedited), `logs/` (run logs where kept).

