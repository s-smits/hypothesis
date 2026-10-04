---
name: screening-and-ranking
description: Choosing, screening and ranking scored entities with at_least, at_most, beats_reference, pareto_front and top_k: which filter a goal's wording calls for, how to set its bar from the inputs' own baseline, and what each assertion proves.
---
# Screening and ranking

## When to use
- "at least / at most / above / below N", a stated limit, "no more than N repeats": at_least or at_most.
- "higher / lower / fewer / more than the first sequence (the original, the input)": beats_reference.
- "find the best N", "the single best", "top candidates": top_k. "Balance A against B", "trade off",
  no weights given: pareto_front. "Keep only sequences that ... and ...": chain two filters.
- Any goal with "find / improve / raise / lower / choose" that must end with fewer entities than it began.

## Shapes
- Stated bar: `scorer -> at_least|at_most {column, threshold}`. The filter port is `items`; read a
  branch as `<step>.yes` or `<step>.no` (a bare filter key is rejected).
- Beat an input: `generator -> scorer -> beats_reference {column, reference, scored_in, higher}` plus
  `baseline: the SAME scorer config on the DAG input that holds the reference`.
- Gate then bar: `check -> at_least 1.0 -> .yes -> bar`, one gate for each requirement the goal states
  (protein_unchanged for "without changing the protein"; length_unchanged only if the goal asks for it).
- Check the kept set: `<f>.yes -> same bar` and assert yes there. Check the dropped set, only when a
  criterion states it: `<f>.no -> same bar` and assert no. Both shapes were accepted in recorded rounds.
- Best N: `pool -> scorer -> top_k {column, k, largest}`; also better than the input:
  `top_k.yes -> beats_reference`, assert yes there. Constrained best: `at_most B -> .yes -> top_k A`.
- Trade-off: `scorer A -> scorer B -> pareto_front {objectives}`; bounded: `front.yes -> top_k`.

## Config that decides the result (ran offline through a real DAG run unless marked)
- `column` is `<node>__<config hash>__<score>`. It must come from a scorer upstream of the filter,
  reached through scorers and filter branches only. A tool step in between clears every column; a
  scorer with other settings makes another column. Both are refused when the plan is checked, naming the
  columns that do reach the filter. Scores survive a filter: a second filter reads the same column on
  `.yes` or `.no` with no re-scoring.
- at_least {column, threshold}: yes is `value >= threshold`. at_most: `value <= threshold`. Both
  inclusive: a value equal to the threshold passes. There is no strict flag (`strict` is rejected).
  NaN goes to no; +inf passes at_least and -inf passes at_most (ran on the node alone; no current
  scorer emits them).
- beats_reference {column, reference, scored_in, higher=True}: yes is strictly above (below when
  `higher` is false) the reference's own score. The reference and every tie go to no (ran: the
  reference sat in no; four variants at its exact score sat in no). `reference` is a whole entity,
  `{"kind":"dna","sequence":...}` (no kind is rejected), and must be one of the inputs. `scored_in`
  names a step scoring that input with the identical scorer config as the candidates' step.
- pareto_front {objectives: {column: true to maximise, false to minimise}}: two or more. Leave `column`
  out (a different one is rejected). yes is the entities no other entity beats on every objective.
  Identical points all stay (ran). Its size follows the pool and the directions: the same five entities
  gave a front of 3 with one direction pair and 2 with another. Dominance is relative to the pool.
- top_k {column, k >= 1, largest=true}: yes is the k best finite scores; ties break by entity id (a
  hash), not by input order. A pool smaller than k keeps everything (ran: k=9 over 5 kept 5).
- Missing column: refused when the plan is checked. At run time every entity in a table has every
  column its source holds (read, not run), so no entity "lacks" one.
- One entity: passes -> yes 1, no 0; fails -> yes 0, no 1. None passes: yes empty, no everything,
  and every step reading `.yes` is skipped. All pass: no empty, so `yes` holds. Empty input: skipped.
- Several objectives. pareto_front when the goal names the objectives and no weights: it can claim that
  no kept entity is beaten on every objective by another entity of this pool; it cannot claim any
  objective's bar is met, a count, or the same set from another pool. Two chained filters when the goal
  gives a bar per objective: every kept entity meets both bars, with no ranking among them. Prioritised
  ("maximise A without letting B pass N"): `at_most B -> .yes -> top_k A`. One weighted score: no node
  adds columns and pareto_front takes no weights, so choose a primary objective and bar the rest.
- Best N. top_k k=N, `largest` false for "lowest". It always returns k entities of the pool, however
  poor (ran: its best sat below the reference), and fewer when the pool is smaller. Size k below the
  pool the generator makes (inputs x variants_per_sequence, less merged duplicates), or it chooses nothing.
- Direction comes from what the number means, in the scorer's own description (describe_node):
  mrna_5prime_mfe more negative is stronger structure, so "stay open" is at_least; gc_deviation and counts
  of unwanted things are at_most.
- A bar from the goal's number: at_least/at_most with that number. A bar from an input: beats_reference,
  nothing typed. Absolute gates may use a number: constraint_check protein_unchanged and
  length_unchanged at_least 1.0, targets_remaining at_most 0.0 (ran); codon_count and motif_count
  at_most 0.0 (read, not run).
- You cannot run a scorer while planning, so a baseline you "work out" is a guess. The instruction to
  "set the threshold relative to the baseline" is met by beats_reference, which reads it from the run.
  Never type the input's score, or that score plus a margin, into at_least or at_most.
- Strict vs non-strict: "above" and "beat" are strict, so beats_reference. "Equal to the baseline
  does not clear it." at_least at the baseline lets the tie through. at_most at baseline - 1 fits only
  integer scores and a measured baseline: use beats_reference with higher=false instead.
- No node takes a margin over a baseline ("10% above"), compares to the best or mean input, compares
  each variant to the input it came from, or adds two columns. Say in the hypothesis what is unchecked.

## Assertions (as the plan check and the run enforce them)
- `yes` holds only if every entity took the yes branch and none the no branch. `no` is the reverse.
  `produced` on a filter holds if yes kept at least one. On a skipped step all three are false.
- The plan check refuses: yes and no on one filter; no with produced on one filter; yes or no on a
  scorer or tool (produced only); a criterion with no assertion. Two criteria may share one step and
  branch, and a recorded round did so.
- Gate whose whole pool should pass (protein_unchanged, a codon gone): `yes` on the gate itself.
- "Every kept entity meets the bar" when the filter splits the pool: `yes` on that filter can never
  hold. 12 recorded rounds asserted it anyway and were rejected. Put a second filter on `<f>.yes` and
  assert `yes` there. The same bar on its own yes branch holds by construction (ran), so the first
  filter's bar must itself be the criterion's bar, in direction and strictness: the verifier reads it
  in the DAG. A different test is stronger: `top_k.yes -> beats_reference` held false when the best of
  the pool sat below the reference (ran), where `produced` on top_k still held.
- "The dropped ones fail the bar", when a criterion says so: same bar on `<f>.no`, assert `no`. If the
  first filter dropped nothing, the audit is skipped and `no` is false (ran): add it only when
  failures are expected.
- `produced` on top_k or pareto_front covers "something was selected", never a count, an order or
  that the selection beats an input. `yes` on top_k holds only when k covers the pool.
- Nothing asserts how many entities were kept, or any per-parent comparison.

## Pitfalls
- Typed baseline. A recorded round filtered at a bar typed from an earlier score: 27 of 104 kept did not
  beat the measured baseline. Two others typed a bar of about 40 against a measured 41.6, and 2 of 49
  kept only tied it. All asserted `produced`, and were accepted. Use beats_reference.
- A bar from another scale. A bar of 1000 passed every entity (40 of 40, 44 of 44, 51 of 51) where
  scores were in the tens of thousands, and none (0 of 39) where they were near 40. These filters
  decided nothing: a bar at or below the baseline does that.
- A bar typed "just above" the baseline kept 0 of 84, 44 and 112: no variant scored above the first
  input, which the builder took to be at the scorer's maximum. An empty yes is then the honest result:
  do not change the scorer's settings or the bar to fill it (one round changed the scorer, typed a
  baseline "not re-measured", and 22 of 78 kept did not beat the real one).
- A baseline step no filter reads decides nothing: it must be `scored_in`, with the same scorer config.
- `scored_in` on a step that reads a tool's output passes the plan check and fails the run ("has no
  score for it"); a scorer with other settings makes another column and is refused.
- Several inputs and "than the first sequence": the whole pool is compared with that one entity, so a
  variant of a stronger input passes on its origin (ran: variants of input 2 all passed).
- All pass is no fault for a gate or when every entity clears the bar; it is the fault when the goal
  says to leave some out. A bar of 0 on a score that cannot be negative, or top_k with k >= the pool,
  chooses nothing. The only guard is at_least on an `expression` column with threshold <= 0, and its
  message says "determine the baseline", which you cannot do: use beats_reference.
- Exactly one input entity and a goal containing higher, lower, more, less, fewer, better, beat,
  increase, decrease, reduce, optimize, minimize or maximize (matched inside words: "wireless") is
  refused unless a generator is in the plan (ran). With no generator the output is a subset of the
  inputs: a find/improve goal needs one, a choose-among-these goal needs top_k with k below the count.
- Not seen in recorded rounds: pareto_front, and top_k beyond a few scripted ones.

## Not here
- Wording of criteria, branch choice for other nodes: `criteria-and-assertions`.
- Which scorer measures the property and its columns: `sequence-scoring`, `expression-and-folding`.
- Making the pool and the protein/length gates: `sequence-recoding`.
- A round that failed: `diagnosing-a-failed-round`.
- A weighted sum of columns, a margin, a count assertion: no node does it.
