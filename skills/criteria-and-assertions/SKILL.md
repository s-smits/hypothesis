---
name: criteria-and-assertions
description: Give every frozen criterion an assertion the run can hold. What produced, yes and no prove, how the verifier and the acceptance rule combine, which claims no node can hold and what to assert and write instead. Read before the first plan of any goal.
---
# Criteria and assertions

Marks: (ran) executed offline here on the nodes and the plan check, no model call; (read) from the code;
(log) a recorded round. What the verifier says about a given plan is a model call and was not run.

## When to use
- Every plan, before the first step. A round cost a median of about 115,000 tokens in recorded runs and a
  run stops at 500,000 (log, read). A plan whose steps repeat an earlier round is refused whatever its assertions
  say (ran), so an audit filter you forgot costs a round: decide every assertion in round one.
- Criteria are frozen before round one: you cannot edit, merge or drop one. A person typed them or edited
  the criteria agent's draft. That agent sees only the goal text and the input kinds (read), so a claim
  never names a node or a score column. Choose the score, copy its column from the node's reply, and name
  the measure in `hypothesis`. `kind` (quantitative, qualitative) does not change how a claim is checked.
- The agent is told to write one claim per stated requirement, none for length, reading frame, start or
  stop codon, alphabet or a bound without a number, and to compare with a number the goal gives or an
  input it names (read). A person's criteria, or a goal that states one of these, can still carry it.
- A vague goal ("better", "optimise") should give a claim with no measure and no number (not seen in
  runs: every recorded goal named its measure). Pick the node that reports the measure from the node
  descriptions, compare with the first input using beats_reference, and name both in `hypothesis`. The
  verifier is told to veto a plan that skips a measure the goal names (read).

## Shapes
Steps joined by `->`, assertion after `=>`. F is the filter that sets the bar, F2 repeats it on F.yes. F2
holds by construction (ran), so the claim is only as strong as F's bar: set F to the criterion's own bar,
in its direction and strictness.
- Measure or convert: <step> => `produced` on the last step. On a converter (dna_to_protein, rna_back_transcribe,
  dna_transcribe, the complements) it covers "the output is that conversion of the input" and nothing beyond (read:
  the verifier is told so). Add no check to a plain conversion; length, residue count or stop symbol is another claim.
- Everything must pass (a gate): <scorer> -> at_least or at_most {column, threshold} => `yes` on it.
- Pool splits, every kept entity clears a bar: F -> F2 (same node, column and bar, reading F.yes) => `yes` on F2.
- The rest are excluded: F3 (same bar, reading F.no) => `no` on F3. Only when F.no is not empty (see Pitfalls).
- Beats the first input: base = <scorer>(the input list); cand = <scorer>(the pool); F = beats_reference
  {column, reference: the first input, scored_in: base, higher} reading cand; F2 on F.yes => `yes` on F2.
- Same protein or length as an input (one input): constraint_check {reference: that input} -> at_least
  {column: protein_unchanged or length_unchanged, threshold: 1.0} => `yes` on it when every candidate must pass.
- Output is not the input: motif_count {motifs: [the input], both_strands: false} -> at_most {threshold: 0}.
  A copy of the input, or any output containing it, goes to `no` (ran). `yes` holds only if none does.
- Exact DNA output E: motif_count {motifs: [E], both_strands: false} -> at_least 1 -> constraint_check
  {reference: E} -> at_least {length_unchanged column, 1.0} => `yes` on the last (ran: right E held, one
  base off did not). For an RNA output run rna_back_transcribe first: constraint_check takes Dna.
- Length bound with a number: codon_count {codons: all 64} -> at_most or at_least on its column, in codons.

## Config that decides the result
- at_least and at_most {column, threshold} keep a value equal to the threshold (ran: a threshold equal to
  the first input's own score kept the first input). Use them for a number the goal gives or a 0/1 column.
- beats_reference {column, reference, scored_in, higher=True}: strict, ties and non-finite go to `no`
  (read). The reference is one of the plan's input entities, scored by the same node in `scored_in`.
  If the reference is among the entities it reads, it fails its own bar and lands in `no` (ran).
- top_k {column, k, largest=True} and pareto_front {objectives: {column: True to maximise}} select a
  subset: `yes` holds only if k or the front covers the whole pool, `produced` if one was kept (ran).
- constraint_check {reference}: one reference serves the whole input list, so one gene per step. Its 0/1
  columns protein_unchanged and length_unchanged carry a hash of the config in their names, so a new
  reference is a new column name (ran): copy it from the node's reply.
- codon_count {codons}: whole in-frame codons from base 1; a partial tail is not counted (ran).
  motif_count {motifs, both_strands=True}: A, C, G, T motifs of 2 bases or more; both_strands true also
  counts each reverse complement, so it cannot tell strands apart (read). gc_content {window} reports gc.

## Assertions
| Branch   | Holds when                        | Does not prove                                    |
|----------|-----------------------------------|---------------------------------------------------|
| yes      | the filter's yes took every one   | anything on a pool that splits: it is false then  |
| no       | the filter's no took every one    | that the criterion is met: it says the bar failed |
| produced | a filter's yes kept one or more;  | what the kept ones hold, how many, or that every  |
|          | any other step gave one or more   | input survived (two inputs, one kept: held, ran)  |
- Only a filter takes yes or no; any other step takes `produced`. yes and no on one filter, or no with
  produced on it, is refused; so is a criterion with no assertion (ran). The `claim` text is never read
  by the code, only by the verifier: say there what the assertion shows and what it leaves out.
- Acceptance, in order (read, ran): every criterion has an assertion; every assertion held, not one per
  criterion, so an extra assertion can only veto; then the verifier must say the assertions cover the goal
  and agree. It can veto and never grant. The verdict names only the first failure: fix an assertion that
  did not hold and a coverage veto in the same round.
- A step whose input is empty is skipped and all its outputs are empty, so any assertion on it fails
  (ran). Look for the first empty step upstream.
- Criteria no node can hold (assert the part a node measures; write the rest once in `hypothesis`):
  - which stop codon, a stop codon kept, a codon at a position, where an ORF sits: no node. Against a
    reference that starts ATG, protein_unchanged 1.0 pinned the start, the residues and a final stop
    symbol (ran: a changed start, a lost stop and a premature stop scored 0.0), yet a stop swapped from
    TAA to TGA scored 1.0 on it and on length_unchanged (ran).
  - a terminal stop absent from a protein: dna_to_protein writes every in-frame stop as `*` (ran). It can
    be held only when the DNA has none: codon_count {TAA, TAG, TGA} -> at_most 0 before the translation.
  - each output against the input it came from: ids hash kind and sequence only and constraint_check and
    beats_reference take one reference, so over a pool from several inputs one reference passes only its
    own gene (ran: 2 of 4). Assert the claim only for a single-input goal.
  - how many entities were kept: nothing counts a set; top_k keeps at most k (ran).
  - starts with ATG: trim_to_first_start outputs begin at the first ATG, whole codons only, and drops a
    sequence without one (ran). Whether the verifier takes `produced` on it as covering a start codon was not run.
  - anything about a protein itself: no scorer reads a protein and a filter needs a score column, so no filter
    branch holds one (read). Assert `produced` on the step that makes it, or a measure on the DNA it came from or goes
    back to (exact string, residue count: sequence-conversion).
- Put the nearest honest assertion on the step the criterion is about, with the claim saying what it
  leaves out. State the gap once: "Unchecked: <criterion id>, <part>, no node measures it." Repeat one
  sentence of it in addresses_critique. Expect a coverage veto, since the verifier's instructions require
  one for an unchecked part (read): it is the true result, so add no step to avoid it.

## Pitfalls
- `yes` on a filter that splits its pool. Pool of 4 with 2 kept: yes false, no false, produced true (ran).
  13 of the 18 recorded rounds that had a failed assertion were this. Assert `produced` on the filter
  and put the claim about the kept ones on F2 (Shapes).
- `produced` on F for "every kept entity clears the bar". It shows one kept entity. The verifier is told
  it never covers such a claim (read). Three older recorded rounds were accepted this way and each kept
  entities that did not beat the first input.
- `no` for a bar the criterion wants met. `no` holds exactly when every entity failed the bar (ran): a
  recorded control asked for a protein of at least 25 residues, the protein had 20, and a `no` on the
  at_least 25 filter was accepted as covering it. The code and the verifier both let it through. Assert
  `yes`, let it fail, and say in `hypothesis` that the input cannot meet the criterion.
- A typed or inclusive bar for a strict claim. A recorded round was accepted on a typed bar well under
  the measured baseline, and about a quarter of the kept entities were not above the first input. Use
  beats_reference for "higher than" and read the baseline from the run.
- beats_reference over a list that includes its reference: `yes` cannot hold (ran). Read the generator's
  pool, or assert `produced` there and the claim on F2.
- `no` audit on F.no when F rejects nothing: F3 reads an empty list, is skipped and `no` is false (ran).
  Add it only where failures are expected. Where all may pass, state the claim on F2's `yes` instead.
- An assertion added "for safety". It must hold too (read): a `yes` on a splitting filter fails the round
  even when the criterion has a good assertion beside it.
- Changing only the assertions in round two: refused as a repeat (ran). Add the filter that carries the claim.
- A pass-through step to look different. codon_count -> at_most in front of dna_to_protein, or
  trim_to_first_start on a sequence that already starts at ATG, handed on the same entity and the output
  was the same (ran). A recorded six-round conversion run was never accepted and spent about 550,000
  tokens, with gates like these in four rounds. A plan cannot end a run: only acceptance, the round or
  token limit or a person does (read).
- Reading protein_unchanged as "nothing else changed". All 10 recorded rounds that were accepted and
  then failed a code check had a changed stop codon that no asserted check measured (see Assertions).

## Not here
- Which node converts, scores, recodes, screens or folds: sequence-conversion, sequence-scoring,
  sequence-recoding, screening-and-ranking, expression-and-folding, protein-and-structure.
- A round that missed, critique, repeats and addresses_critique: diagnosing-a-failed-round. The loop's
  stages and limits: hypothesis-loop.
- No node can: say which stop codon a sequence ends in, read a codon at a position, pair an output with
  its source, count the entities in a set, or judge a claim that no score or count expresses.
