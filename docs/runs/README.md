# Recorded runs of the hypothesis loop

Everything here was copied, read-only, from the places the runs were saved (results directories, the overnight evidence folder, scratch worktrees). The files are the loop's own records: values below are copied from the saved Hypothesis files and ledger lines, not recomputed or judged.

## Layout

- `<fork>/<group>/` where `<fork>` is the PR the run's commit was forked from, decided with `git merge-base --is-ancestor` against the PR tips (see each group's README for the table): `pre-pr7` (a recorded commit older than every #7 head), `pr7` (a #7 head or a commit built on one), `unknown` (no commit recorded; the evidence is in the group README).
- Inside a group: `hypotheses/`, `workflows/`, `trajectories/<id>.jsonl.gz`, `ledger.jsonl`, `logs/`, `scripts/`, `README.md`.
- Python helper scripts under `scripts/` and `packs/` are stored as `*.py.txt` (content byte-identical) so that lint, type checks and test collection ignore them: copy a file and drop the `.txt` to run it.
- `index.json`: one row per run (and per benchmark attempt) with source path, commit basis and caveats. `benchmark-ledger/`: the five benchmark attempts (two baselines, three loop records). `goals/`, `scripts/`, `packs/`: the goal inputs, helper scripts and the prepared (never run) replication and codon-pair packs, without their scoring oracle.

## Fork points
Arms E, F and F2 sit under `pr7`: their commits (74803ee, bb7cf40, 07f1f87) contain `dc1916a` (the #7 head, which includes #8) and are not ancestors of the #9, #11, #12 or main tips; their content (beats_reference, the integrated patches, the prompt tightening) was rebuilt later as new commits in #9, #11 and #12, so by ancestry they were forked from #7 and their lineage is #11. Runs whose commit no file records are under `unknown`, with the nearest evidence.

## Live-model runs

| group | run | goal | models | fork PR | commit | rounds | tokens | outcome | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pr7/arm-A-c1ed566 | 26acc0f5 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | c1ed566 | 3 | 460705 | not achieved | 3 |
| pr7/arm-A-c1ed566 | 45b928e8 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | c1ed566 | 3 | 369784 | achieved | 3 |
| pr7/arm-A-c1ed566 | 6dc8b1bd | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | c1ed566 | 3 | 358751 | not achieved | 3 |
| pr7/arm-B-88a44b9 | 2adaf0fd | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | 88a44b9 | 1 | 83625 | achieved | 3 |
| pr7/arm-B-88a44b9 | ad7f1624 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | 88a44b9 | 2 | 230394 | achieved | 3 |
| pr7/arm-B-88a44b9 | f065baab | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | 88a44b9 | 2 | 240931 | achieved | 3 |
| pr7/arm-C-dc1916a | a9d91571 | ostir | sonnet-5-5 | 7 | dc1916a | 3 | 403297 | abandoned | 2 |
| pr7/arm-C-dc1916a | e5917e38 | ostir | sonnet-5-5 | 7 | dc1916a | 3 | 431892 | not achieved | 2 |
| pr7/arm-E-74803ee | 6148f02d | arfA-cai | sonnet-5-5 | 7 | 74803ee | 3 | 569667 | achieved | 1 |
| pr7/arm-E-74803ee | 88c26e22 | ostir | sonnet-5-5 | 7 | 74803ee | 3 | 437695 | not achieved | 1 |
| pr7/arm-F-bb7cf40 | 14535b56 | ostir | sonnet-5-5 | 7 | bb7cf40 | 2 | 189150 | achieved | 1 |
| pr7/arm-F-bb7cf40 | 5fec1ed4 | arfA-cai | sonnet-5-5 | 7 | bb7cf40 | 1 | 102853 | achieved | 1 |
| pr7/arm-F2-07f1f87 | 894a1eba | arfA-cai | sonnet-5-5 | 7 | 07f1f87 | 1 | 144385 | achieved | 1 |
| pr7/arm-F2-07f1f87 | ce899c7c | ostir | sonnet-5-5 | 7 | 07f1f87 | 2 | 232256 | achieved | 1 |
| pr7/loop-tip-37e8de9 | 3bec2a48 | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | 37e8de9 | 3 | 313544 | abandoned | 1 |
| pr7/loop-tip-89962aa | bead95ef | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | 7 | 89962aa | 3 | 297109 | not achieved | 1 |
| pre-pr7/compact-be3805f | 9afcd59e | ostir | plan sonnet-5-5, verify haiku-4-5-20251001 | pre-7 | be3805f | 2 | 170098 | abandoned | 2 |
| pre-pr7/compact-be3805f | d10361e4 | ostir | not in a ledger line | pre-7 | be3805f | 5 | 445053 | no result | 2 |
| unknown/compact-atom-count | f8529510 | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | unknown | unknown | 3 | 221358 | achieved | 5 |
| unknown/compact-atom-count | f1bbba3e | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | unknown | unknown | 3 | 222822 | achieved | 5 |
| unknown/compact-atom-count | 6b82eb8e | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | unknown | unknown | 3 | 238310 | not achieved | 5 |
| unknown/compact-atom-count | bbe2cc75 | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | unknown | unknown | 3 | 183363 | not achieved | 5 |
| unknown/compact-atom-count | 5bf5f3e1 | atom-count | plan sonnet-5-5, verify haiku-4-5-20251001 | unknown | unknown | 3 | 201149 | not achieved | 5 |
| unknown/e2e-atom-count-ce59ac5b | ce59ac5b | atom-count | not in a ledger line | unknown | unknown | 2 | 89491 | achieved | 1 |
| unknown/e2e-recode-acg-mock | recode-acg-mock | recode-acg-mock | not in a ledger line | unknown | unknown | 1 | 74854 | no result | 1 |

## Scripted runs (51; no live model call)

| group | runs | achieved | not achieved | no result |
| --- | --- | --- | --- | --- |
| pr7/hostile-probe-bb7cf40 | 40 | 18 | 22 | 0 |
| unknown/compact-remove-tcg | 2 | 1 | 1 | 0 |
| unknown/scripted-fixtures | 9 | 7 | 2 | 0 |

## Reading these

- `n` is the number of runs in the same group with the same goal. Every cell is small (1 to 3 runs); these are not rates.
- ostir 'achieved' means the verifier judged the kept sequences higher than the first input, not a lift over the best input.
- A run's `outcome` is derived from its saved state: achieved, not achieved, abandoned; blocked, building, running and failed count as no result.
- Per-run caveats and sources are in `index.json`. Where a run's commit is not recorded the folder is `unknown`; a candidate commit is named only as evidence.
- Not copied: `nodes/` and `registry/` caches, environments, databases, credentials, the packs' scoring oracle, byte-identical copies of the arm folders, and state-only UI fixtures.
