---
name: diagnosing-a-failed-round
description: Round two and later of the builder: read the critique, held and the produced preview, tell a wiring or config fault from a coverage fault or a criterion no node can meet, then change the plan by the smallest step that is not a repeat.
---
# Diagnosing a failed round

Marks: (ran) executed offline on small inputs, (read) taken from the code, (recorded) seen in a recorded round, told in words.

## When to use
- "Attempts so far" is in the prompt: you are planning round two or later.
- A typical round costs about 115,000 tokens (recorded: the usual run averaged that, the range roughly 45,000 to 190,000) and the run's
  the token cap (default 500,000) is checked before each round, so a run gets about five rounds, not the 20 it allows
  (read). Be right in the round you are in: two or three rounds on one fault is most of a run.
- A wasted round costs the same as a useful one. A round that adds gates which pass the same entity through cost 90,000 to 215,000
  tokens (recorded); a round that ends with no valid plan (three failed output retries) cost 110,000 to 135,000. Its critique is
  synthesized (`goal_misread`, "ran out of output retries"): it says nothing about a plan, so read the round before it.

## Shapes
Read the critique's `diagnosis` before its `root_cause` label. Of 46 recorded critiques about a plan, 13 said the pipeline did what the goal asked and
only the assertions fell short, under the labels wrong_wiring (7), missing_tool (4, not offered when requests are off), wrong_config and goal_misread (1 each);
wrong_config (16 in all) was usually one typed number; `node_raised` was never seen. Then read the last round in this order and take
the first line that fits. Keep every step the critique lists in `keep` exactly as it was.
1. `error` set, `held` and `produced` empty: a node raised on this round's data (read: the deepest cause is shown). Change what reaches that node or its config; add nothing after it. Seen offline (ran): esmfold2_fold
   "A stop codon mid-sequence is not one chain"; beats_reference "'gcs' has no score for it ... Score the reference ... in a step that
   runs first and reads the input that holds it, and name that step in scored_in". A bad config field is caught while the plan is
   checked (read), before any round is spent.
2. Verdict agrees, coverage False, every `held` True (coverage veto): the values were right. Read the verdict's reason for what
   went unchecked and add only that, on the branch that carries it. Three recorded runs were accepted one or two rounds later (the
   cheapest at about half a typical round) after adding filters there: a re-check of the kept set by constraint_check then at_least on `protein_unchanged` or
   `length_unchanged` (assert `yes`), or the selecting filter again over its `no` branch (assert `no`). Re-applying the selecting
   filter's own bar to its `yes` branch holds by construction (read: the verifier is told it adds nothing to the first filter's bar);
   other recorded rounds were vetoed as a tautology for it, so one accepted copy is no rule. Prefer a different measure; never stack copies.
3. The final filter's `produced` is False and the pool was already wide: see Config, pool ceiling. Do not widen it again. If the
   values show a random generator's whole pool on the wrong side of the bar, change to a generator that targets the measure
   (codon_optimise, gc_target_recode, recode_targeted: see sequence-recoding); a recorded plan did this after one such round and was
   accepted in the next, at under half a typical round. If none targets it, say in `hypothesis` that no variant clears the bar.
4. A threshold was typed (verdict or critic says "guessed", "stale", "hand-typed"): score the first input in a step of its own, then
   `base=<scorer>(input)`, `cand=<scorer>(pool)`, `better=beats_reference(column=<scorer column>, reference=<first input>, scored_in=base)`.
5. A criterion contradicts what a node writes (Pitfalls 8): say so once, assert only what holds, add no gates.

## Config that decides the result
- What counts as a new wiring (ran): node string, config, inputs and step keys. Hypothesis, expected, `why`, assertions and their claims
  are not compared. So moving an assertion alone is refused ("This wiring already ran in an earlier round. Change it."), and a changed
  seed on a step the critique kept passes and fixes nothing (recorded: only a generator's seed and counts changed; the critic said the
  earlier critique "went unheeded"). Change the step the critique faults, or add a step.
- beats_reference: `column`, `reference`, `scored_in`, `higher` (default True). Strict: ties go to `no` (read). The column is
  `<node>__<config hash>__<score>`, so a scorer with a new config has a new column and a new baseline: the first sequence's score moved
  by roughly 40 percent when a recorded plan changed the scorer's UTR setting, and the typed threshold from before no longer meant anything.
  Re-measure with a step of its own; never carry a number across a config change.
- at_least / at_most are inclusive (read). A typed threshold below every score keeps all and decides nothing; above every score it keeps
  none (recorded: several plans guessed low, one guessed high and `keep_better.yes` came out empty).
- mutate_synonymous pool ceiling (ran): `count` swaps exactly that many codons and equal results merge into one entity, so variants stop
  being new once `variants_per_sequence` passes the number of distinct results. Alternatives per codon are its synonyms minus one. For
  three swappable codons with 3, 5 and 1 alternatives, 500 draws gave 9 distinct sequences at count 1, 23 at count 2 and 15 at counts 3
  and 4 (count 3 gave 15 at 200 and at 2,000 draws too). Work the ceiling out from the input before widening, and set `count` to reach more.

## Assertions
- `held` keys are `step.branch`. A `yes` that is False does not mean the branch is empty: it is False whenever the `no` branch also took
  entities (ran: a filter that split four sequences two and two gave `better.yes` False, `better.no` False, `better.produced` True).
  A recorded round lost to this had the verifier agreeing and covering, a few kept out of dozens, and the next round re-tuned a
  threshold that was fine. For a filter that splits a pool, assert `produced` on it; put "every kept" on a second filter over `.yes`.
- An empty input skips a step: its outputs are empty and everything downstream holds False (ran: trim_to_first_start on a sequence with no
  ATG gives nothing). Find the first `[]` in `produced` and fix there, not at the last filter.
- `produced` shows up to three entities per source as `head12..tail6 (n)`, n being the sequence length, not a count, and no scores.
  A step whose line equals its input's line changed nothing you can see (ran: trim_to_first_start on a sequence that starts with ATG,
  same entity id). A filter with `.no` `[]` and `.yes` equal to its input decided nothing, a fault only if the goal says something must go.
- Earlier rounds carry no scores: only the verdict and critique text carry numbers, and either can be wrong. A recorded verdict called a
  threshold a hair above the baseline (fourth decimal) lower than it, two rounds running, and the critic corrected it. Check a number against `held` and the other text.
- An assertion is a branch claim (`produced`, `yes`, `no`) on a step, nothing finer. A critic asking for "the minimum score in
  `better.yes` exceeds X" cannot be written (recorded, several runs); write a filter over that branch and assert its `yes`.

## addresses_critique and keep
- Non-empty is required whenever the last round has a critique, and only its presence is checked (read). Say in order: the critique's claim
  you accept or dispute, with the value that decides it; each step key added, dropped or changed and what it now does; what stays as
  `keep` lists; where each number comes from (a step's column, not a typed value). Good (recorded, accepted next round): "Assertions are
  rebound to filter branches ... A second beats_reference filter on better.yes carries the yes assertion ... The steps and values are unchanged."
- Claim only what the wiring does. A recorded plan said a new gate made the criterion "rest on a content check"; the gate passed the same
  entity (ran). Another called a typed number "the likely baseline"; it was another sequence's score.
- `keep` lists every step (recorded: coverage vetoes): the fix is an addition. `keep` is short: change only the unlisted step (recorded: a
  critique that left out the faulty filter, a plan that changed only it, values right next round).

## Pitfalls
1. Re-wiring with the same effect. A recorded run put codon_count + at_most (then trim_to_first_start) before dna_to_protein: the same entity
   passed, `.no` was `[]`, the translation kept its `*` (ran). Such a round cost 90,000 to 215,000 tokens and changed nothing. A step
   that only reads cannot change what follows it.
2. Respelling a repeat. The plan check compares text: a registered node id in place of the bare name, or renamed step keys, passes it
   (ran). The critic still called each such plan a repeat, and the recorded run spent two more rounds that way. Compare by effect.
3. A look-alike node. trim_to_first_start was added to drop a trailing stop; it trims before the first ATG (and a trailing partial codon), a no-op on an
   in-frame sequence that already starts there (ran). Read what a node's entry in `nodes` says it changes before relying on it. Swapping a scorer's setting for another
   moves the baseline (Config), so the plan has to re-measure rather than reuse.
4. Over-engineering a plan whose values were right. Recorded re-verification chains: a second pass of the same bar on `.yes`, a rescore
   with the same scorer then the same filter ("tests nothing new", said the critic), a twelve-assertion three-branch plan that never ran.
   All were vetoed or blocked. Add one step per unproved claim (Shapes 2), by a different measure where one exists.
5. Moving a threshold the values did not call for. A recorded plan nudged a typed cutoff in its fourth decimal and added gates when the previous
   round had split the pool with the tie in `no`: the fault was a `yes` assertion on a splitting filter. Before editing a threshold, check that `no`
   holds the tie and every kept score beats the baseline; if so, leave it.
6. Widening an exhausted pool. Recorded: a larger pool with a new seed gave an empty set; a tenfold wider pool added some unique
   sequences and still left none above the bar; both critics said the space was spent. Count the ceiling (Config), say in `hypothesis` that no variant clears
   the bar, and assert on the filter's `no` branch what that shows.
7. Doing what the critique cannot express: per-gene inputs and an entity selector, a start-codon or fixed-index check the goal states, an edit-distance
   scorer. Check each fix against `nodes` and the assertion branches. Do the part that can be written and say in `addresses_critique`
   which part no node can show; do not name or request a node. Most recorded runs that hit a coverage veto were never accepted after it;
   the three that were accepted had added filters on the claim's branch.
8. A criterion the output contradicts. Criteria now hold only what the goal states, and the verifier is told that `produced` on a
   converting step covers "the output is that conversion" and on a DNA-making step covers the DNA alphabet (read), so a plain conversion
   needs no further assertion and no gate. A clash is left when the goal itself asks for what no chain can write. Before that change a
   recorded run showed the cost: the translation was right in round one, dna_to_protein writes the terminal stop as `*` (ran), no node
   trims a 3' end, a criterion asked for no stop symbol, and five more rounds failed it at a typical 50,000 to 215,000 tokens each.
   Name the criterion, the node whose output contradicts it, and the value. If no step can change that value, say so once in
   `hypothesis`, `expected` and `addresses_critique` and assert only what holds. The loop ends only on success, the round limit,
   the token cap or an abandon (read), so add no gate to look different.
9. Trusting the verdict over the values. A correct threshold was judged "lower" by an arithmetic misread, and again after a new check was
   added. Hold the threshold, quote the comparison in `addresses_critique`, add only a step that tests something new.
10. Failing the plan check three times. It shows only the first failing check each try: an uncovered criterion, `yes` and `no` on one
    filter, a repeat, an empty `addresses_critique`. Check all four before submitting.

## Not here
Wording criteria and choosing assertion branches: criteria-and-assertions. Choosing a generator, scorer or filter: sequence-recoding,
sequence-scoring, screening-and-ranking. The loop's stages: hypothesis-loop. A check no node performs: no node can do this.
