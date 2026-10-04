# pr20/arm-H-822d2ea

Runs: 2. Kind: live. Commit: `822d2ea` (`822d2ea422e8d229c9b3b20db542dcfaf374526b`). Fork PR: 20. Lineage: the #20 head (`codex/sweep-findings`), which merges #14, #17, #15, #16, #19 and the loose-ends, result-binding and evidence-repair branches onto #12's head 46e125f.

Commit basis: worktree $S/arm-H: `git rev-parse HEAD` = 822d2ea, clean; arm_H.status 'code 822d2ea'; scripts/run_armH_ran.sh header.

Setup: Sonnet 5.5 builds, verifies and critiques; max 8 rounds; guard cap 500,000 tokens per run, ceiling 1,000,000; two runs at once on their own Temporal server (port 7241). A run blocked on a node request for 15 minutes is abandoned by the guard (nobody writes the node). Total spend 813,535 tokens. Predictions P9 (H1) refuted and P10 (H2) untriggered; no round was accepted.

Ancestry (git merge-base --is-ancestor, in the repo object store): commit in tip / tip in commit.

| tip | commit is an ancestor of tip | tip is an ancestor of commit |
| --- | --- | --- |
| 8 | False | True |
| 7-old-2687572 | False | True |
| 7-old-c1ed566 | False | True |
| 7 | False | True |
| 9 | False | True |
| main | False | True |
| 11 | False | True |
| 12 | False | True |
| 14 | False | True |
| 17 | False | True |
| 19 | False | True |
| 20 | True | True |

| run | goal | models | rounds | tokens | state (saved) | ledger state | n | caveats |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7310327f | aroE-cai | sonnet-5-5 | 4 | 568743 | not achieved | not achieved | 1 | 'not achieved' is the loop's state: the 500,000-token cap was reached at the start of round 5 (568,743 spent, plan 518,524). Round 1 produced a result that is right by code (score_run.py: 1 kept, above the first input's 0.638744, stop codon unchanged, the immutable-honouring gate passes 1 of 1) and was vetoed because fixed_codons_kept had only a produced assertion (covers_goal false); rounds 2 to 4 each ended 'no valid plan: Exceeded maximum output retries (3)' on a request for a fixed_codon_check node (replay_H1.py reproduces the guard ladder through check_plan offline, no model call); goal file: the goal_for(aroE) goal with the five criteria (the #17 emit), as in packs/replication/goals/goal_aroE.json plus fixed_codons_kept; n=1, one dev gene, in-sample; prediction P9 refuted |
| ff471dd9 | ostir | sonnet-5-5 | 2 | 244792 | abandoned | abandoned | 1 | 'abandoned' by the guard after the run had been blocked for 15 minutes on a node request (requests/terminal_codons_check.json), 244,792 tokens, 2 rounds. Round 1 (vetoed, covers_goal false because valid_dna_output had no start or stop evidence): 35 mutants, 16 kept, all above the first input (lowest 2139.08 against 2055.2) and 0 of 16 with a stop codon different from their parent's by score_run.py; the verifier's reason said several variants end in TAG or TGA, which are the stops of their own parents; goal file: arm_goal.json, the ostir goal of arm F2 (pr7/arm-F2-07f1f87/ce899c7c); n=1; prediction P10 untriggered (no accepted round) |

Files: `hypotheses/<id>.json` (the saved Hypothesis), `workflows/<id>-r<round>.json` (the executed DAG of each round that ran one; rounds that ended without a plan have none), `trajectories/<id>.jsonl.gz` (each line is one model-call transcript: `{file, data}`; gzip, mtime 0), `requests/` (the node request the run was blocked on), `ledger.jsonl` (unedited), `logs/`, `scripts/` (`replay_H1.py.txt` replays a round's recorded plan attempts through the real plan guards with a scripted model, offline; `score_run.py.txt` is the code-only scorer; both stored as .txt, drop the suffix to run).
