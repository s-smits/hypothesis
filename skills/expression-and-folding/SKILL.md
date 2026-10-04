---
name: expression-and-folding
description: Plans goals that measure, raise, lower or bound translation initiation (ostir_expression), 5' mRNA structure (mrna_5prime_mfe) or whole-mRNA folding (mrna_fold_energy) for a CDS, a leader plus CDS, an RNA or a protein.
---
# Expression and mRNA folding

## When to use
- "Expression", "translation initiation rate", "ribosome binding", "RBS strength": ostir_expression (its
  column is named `expression`). "5' structure", "open start codon": mrna_5prime_mfe. "mRNA folding",
  "secondary structure", "stability": mrna_fold_energy. Use the node the goal names.
- Codon usage, CAI, GC, motifs, repeats are not this family (see Not here).
- Inputs: a DNA CDS that starts with a start codon, an RNA, DNA with a leader before the ORF, a protein.

## What the nodes read (all three were run offline)
- ostir_expression: `utr` has no default and goes in front of every entity; only the entity's FIRST codon
  is scored as the start. It reads the last 35 bases of utr and the first 35 bases of the CDS: changing any
  other base never moves the score. `expression` is higher = faster initiation, on an arbitrary scale.
  A first codon of ATG, GTG or TTG scores; any other first codon scores 0.0, not an error: 0.0 means
  "no start here", not "weak". DNA and RNA entities score alike. About 1 ms per entity.
- mrna_5prime_mfe: folds `utr` (default "") plus the first `cds_bases` (default 60) bases of each entity from
  its first base. `mfe` in kcal/mol is never above 0: more negative = more structure, higher = more open.
  About 1 ms per entity. An empty entity scores 100000.0.
- mrna_fold_energy: folds the whole entity, no config and no utr. Columns `mfe`, `ensemble_energy`,
  `mfe_per_base` (use this to compare lengths). Cost is cubic: one entity takes 0.15 s at 300 nt, 0.9 s at
  600, 5.8 s at 1.2 kb, 35 s at 2.4 kb, 200 s at 4.8 kb. A step runs its whole list under one time limit
  (20 min here, 5 for the other two), so N variants cost N times that.
- The mfe scores are single precision (-12.3 comes back -12.300000190734863): an entity sitting exactly on a
  typed bar can land on either side.
- Entities with the same kind and sequence are one: duplicates in an input or a pool collapse (90 variants
  of three 15-nt inputs came back as 42). A DNA and an RNA entity with the same bases are two.

## Shapes
- Measure or rank what is given: seqs -> scorer. Add top_k (k=1 for the best) or at_least / at_most when the
  goal states the bar as a number. A band or target: at_least, then at_most on its `.yes`, one column.
- Raise or lower against the input ("the first sequence" is the baseline; several inputs share one pool):
  seqs -> resample_synonymous -> constraint_check -> at_least gates -> scorer -> beats_reference, with
  `reference` the first input, `column` the scorer's column, `scored_in` a step that is the SAME scorer (same
  utr, anti_sd, cds_bases) on seqs, `higher` True to raise, False to lower (both directions work).
- One sequence and a "raise/improve" goal needs a generator. The plan check only knows some comparative
  words ("raise" is not one), so do not wait for it. trim_to_first_start counts as a generator to the plan
  check but makes none.
- Two objectives against the input: scorer A -> scorer B (both columns travel on) -> beats_reference on A
  -> `.yes` -> beats_reference on B, one baseline step per scorer. With no baseline and a real
  trade-off: both scorers -> pareto_front with objectives {column: True or False}.
- RNA input: scorers take it as is. rna_back_transcribe before any generator (they take Dna only). Score
  the RNA input directly for the baseline and give the Rna entity as `reference`; dna_transcribe the
  kept set if RNA is wanted.
- Protein input: protein_to_dna (codon_weights is required, taken from the goal; `{}` is accepted and takes
  each codon family's first) -> resample_synonymous -> scorer. It makes one CDS per protein.
- Leader plus CDS input: see the pitfall on leaders.

## Config that decides the result
- ostir `utr`: upper-case DNA, non-empty; empty, lower-case or RNA is refused when the plan is checked. Use a
  leader the goal or input gives, verbatim. Otherwise write one 35 bases long with a Shine-Dalgarno (GGAGG)
  5 to 7 bases before the start and A/T filler elsewhere, say so in the hypothesis, and keep it in every step
  and round. Scores move by orders of magnitude with the spacing and the SD, and not monotonically (a scan of
  the spacing peaked at 5 to 7 bases and fell off by 12), so a score means something under one utr only.
- ostir `anti_sd`: 9 bases, default E. coli ACCTCCTTA. Another value changes every score (about twofold
  on one CDS); set it only for another organism or an orthogonal ribosome.
- mrna_5prime_mfe `utr`, `cds_bases`: bases past `cds_bases` are ignored. The utr is an assumption unless
  the goal gives it; with no utr only coding bases fold.
- resample_synonymous `variants_per_sequence`: redraws every codon, so each variant gets a fresh window.
  mutate_synonymous `count`: swaps only that many random codons anywhere in the CDS. Both need `seed`.
- beats_reference `higher`: True raises. It is strict: a tie, the reference itself included, goes to `.no`.
  at_least and at_most are inclusive (>= and <=).

## Assertions
- Score-only goal: `produced` on the scorer.
- "Higher or lower than the first": make beats_reference the filter that carries the bar, with `higher` the
  goal's direction, and assert on it. A typed at_least threshold covers such a criterion only if the goal
  gave that number. An assertion of `produced` on beats_reference was vetoed.
- When the goal keeps the protein: assert `yes` on the at_least gate over constraint_check's
  `protein_unchanged` (its `reference` is the input CDS), not on the generator.
- Stability, half-life or yield wording: no node reports half-life or yield. Use mrna_fold_energy and say in
  the hypothesis which direction of `mfe` you took to stand for the goal, and that the rest is unchecked.

## Pitfalls
- A typed bar fails. Scores do not exist at planning time, so "baseline times something" is a guess. Typed
  at_least/at_most bars on ostir often failed: a bar far below the measured baseline kept every variant, and
  a bar carried over after the utr changed accepted variants not above the new baseline. beats_reference
  against a measured baseline did not have this problem. Type a bar only when the goal states the number.
- Changing the utr moves the baseline and voids any bar from an earlier round. Leaders chosen by the
  builder for one goal gave baselines more than four orders of magnitude apart.
- A utr has a ceiling: the rate of a CDS that folds nothing with it (no score in 2400 random trials exceeded
  the bare "ATG" under the same utr). If the baseline sits there, no recoding beats it: for a 15-nt
  CDS the whole 48-spelling space can top out at the baseline. The yes branch then stays empty and the verifier vetoes it. Say in the
  hypothesis that the baseline is at the ceiling for that utr; do not widen the pool or vary the seed.
  A new utr is a different experiment: if you switch, re-measure and say so.
- Long CDS: only about 11 codons after the start can move ostir. mutate_synonymous with count 1 to 3 on a
  900-nt CDS left about 90 percent of variants tied with the baseline; count at or above the swappable codons, or
  resample_synonymous, left few tied (ran). The share of swaps inside the window is
  about 11 x count / swappable codons. No node confines edits to a window.
- Short CDS: the space is the product of each codon's synonym count (15 nt: 48), so size
  `variants_per_sequence` to it (drawing 60 for a 15-nt CDS gave 43 unique).
- Folding a short CDS is degenerate: a 15-nt CDS gave 0.0 in all 48 spellings on mrna_5prime_mfe (no utr) and
  mrna_fold_energy, so "less structured" has nothing to separate; "more open" cannot beat a 0.0 baseline.
- Leader plus CDS. ostir_expression on the whole entity scores 0.0, since its first base is the start. Generators
  read the whole entity as codons, so on it they rewrite the leader, and with a leader length that is not a
  multiple of 3 they changed the CDS's protein in every variant tried (ran). The route is `trim_to_first_start ->
  generator -> ostir_expression`, with `utr` the leader the input or the goal gives (inputs must share it).
  Trimming changes the sequence (the leader is gone, the first ATG becomes base 1); say so in the hypothesis.
- A baseline of 0.0 is hollow. Against the raw leadered input, which scores 0.0, every trimmed sequence wins:
  40 of 40 resampled variants beat it, but only 10 of 40 beat the same input trimmed and not recoded (ran). Trimming
  alone moved one input from 0.0 to tens of thousands. Measure the like-for-like baseline in a step of its own:
  `trim_to_first_start -> ostir_expression`, the same `utr` and `anti_sd`, no recoding, and read its number. It
  cannot be the `reference` of beats_reference (see "No baseline after a trim"); state the number in the
  hypothesis, and type a bar from it (`at_least`, which lets a tie pass) or use `top_k`, only under that same
  `utr` and cut.
- Which ATG. trim_to_first_start cuts at the first ATG in any frame, even one inside a restriction site of the leader
  (TCTAGATG), and the ORF from there can be another frame and protein than the one the goal means: with a
  leader of 42 or 43 bases the first-ATG ORF translated to a different protein than the later ORF, and the later
  start scored about a tenth of the trimmed one (ran). Before planning, read the input: the ATG with an RBS-like
  region just upstream and the protein the goal means. If it is the first ATG, trim. If it is a later one, no node
  cuts there (read): name the mismatch in the hypothesis and do not claim the intended protein is kept.
- No baseline after a trim. `reference` must be an input and its `scored_in` step must score that input as it is.
  With `scored_in` a step after `trim_to_first_start`, the input as `reference` passes the plan check and fails in the
  run ("has no score for it"), and the trimmed CDS as `reference` is refused by the plan check (ran). A protein
  input has no such baseline either (read). mrna_5prime_mfe with `utr` "" and `cds_bases` covering the leader plus
  about 35 bases scores each input with its own leader (read), though no generator keeps a leader fixed.
- mrna_fold_energy on a kb-scale pool is slow (see timings). When only the start matters use mrna_5prime_mfe.
- beats_reference is strict and no filter means "at least as good as the input". "Without worsening X"
  can only be "strictly better" or a stated number.
- What is said here about mrna_5prime_mfe and mrna_fold_energy comes from running them, not from past goals.

## Not here
- Codon usage, CAI, codon pairs, GC, motifs, repeats, dinucleotides: sequence-scoring. Recoding to a
  target (codon_optimise, recode_targeted, domesticate): sequence-recoding. Conversions: sequence-conversion.
- Filters and ranking in depth: screening-and-ranking. Assertion wording: criteria-and-assertions. A round that
  missed: diagnosing-a-failed-round. Structures: protein-and-structure.
- No node can: design or vary a UTR or RBS (one utr per step); score a start that is not an entity's first
  base or find the true start; predict half-life, decay or protein yield; rank by distance to a target;
  keep a leader fixed while recoding; restrict edits to a position window; merge two lists into one score.
