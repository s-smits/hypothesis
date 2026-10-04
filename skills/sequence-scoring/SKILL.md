---
name: sequence-scoring
description: Measuring composition and codon use with gc_content, codon_count, codon_adaptation, codon_pair_score, dinucleotide_bias, motif_count, repeat_score and constraint_check: which fits a goal, their columns, filters on them, checks after a recoding.
---
# Scoring sequence composition and codon use

## When to use
Pick the scorer by what the goal hands over and names, not by its verb.
- gc_content: "GC content/fraction/percent", "GC-rich or AT-rich stretches", "GC between a and b".
  Not: third-position GC (it counts every base), CpG (dinucleotide_bias).
- codon_count: the goal names codons to count, remove, free or add ("no TCG remains", "more ATG").
  Not: a restriction site or motif, which can sit across codons (motif_count).
- codon_adaptation: "CAI", "codon usage/optimality" given per-codon weights (3-letter keys).
  Not: a pair table (codon_pair_score), or codons named for removal with no table (codon_count).
- codon_pair_score: "codon pair score/bias" given pair weights (6-letter keys, codon then codon).
  Not: CpG across a codon boundary (dinucleotide_bias).
- dinucleotide_bias: "CpG", "UpA", depletion or observed/expected, in any frame.
  Not: a count of sites (motif_count) or in-frame codon pairs (codon_pair_score).
- motif_count: "restriction site", "recognition sequence", "forbidden motif", "homopolymer run".
  Not: removing the site (a recoder) or base composition (gc_content).
- repeat_score: "repeats", "repetitive", "hairpin/inverted repeat", synthesis feasibility.
  Not: named sites or one-base runs (motif_count), folding energy (expression-and-folding).
- constraint_check: "without changing the protein", "same length", "named codons gone" after a
  generator. Not: a quantity to optimise. It compares candidates with one reference.
"Optimise codon usage": per-codon weights -> codon_adaptation; a pair table -> codon_pair_score;
codons to remove or add -> codon_count. If the goal names a measure, use that one, not a look-alike.
No table in the goal or a kept observation: nothing real fills `codon_weights`; say so in the
hypothesis. CpG "depletion/ratio" -> odds_ratio; "number of CpG" -> motif_count motifs=("CG",).
One-base runs -> max_homopolymer; longer ones -> repeat_score.

## Shapes
- Measure and report: `seqs -> scorer`. Nothing else.
- Screen: `seqs -> scorer -> at_most|at_least` on one of the scorer's columns.
- Beat the first input: `base = scorer(seqs)`; `generator -> [constraint_check -> at_least 1.0
  gates] -> scorer (same node id as base) -> beats_reference(reference=first input, scored_in=base,
  higher=...)`.
- Prove a recoding: `recoder -> codon_count|motif_count|gc_content|constraint_check -> filter`.
- Scorers chain: every upstream scorer's columns stay on the table and on `.yes` and `.no`.

## Config that decides the result
Columns are `<node>__<config_hash>__<score>`; scores are floats; fractions are 0-1, not percent.
- gc_content: `window` (required, >=1, stride 1, not codon-aligned; longer than the sequence = one
  window), `target` (default 0.5, only for gc_deviation). Columns gc, gc_min, gc_max, gc_deviation
  (lower = nearer target). DNA or RNA.
- codon_count: `codons` (>=1, upper-cased, de-duplicated; a listed stop counts, the last too).
  Column `count`, in frame only (AAATGGCCC has no ATG). Lower for "remove", higher for "more". Dna.
- codon_adaptation: `codon_weights` (required; only ratios within an amino acid matter, a x30
  table gave the same CAI). Column `cai`, 0-1, higher better, 1.0 = every codon the table's
  favourite. Dna only.
- codon_pair_score: `pair_weights` (6-letter keys), `missing` (default 0.0, weight of an absent
  pair). Column `codon_pair` = mean weight over in-frame adjacent pairs, unbounded; which way is
  better follows the table, so take it from the goal ("raise the score" -> higher). Dna only.
- dinucleotide_bias: `dinucleotides` (>=1, ACGT only, so TA for UpA; "UA" is refused). Columns
  `odds_ratio` (observed / expected from the sequence's own bases, pooled over the pairs; 1.0 =
  chance, below 1 depleted) and `frequency` (per adjacent pair, composition-blind). DNA or RNA.
- motif_count: `motifs` (>=1, ACGT only, >=2 bases, no N/R/Y), `both_strands` (default True:
  reverse complements count too; a palindrome once). Columns `motifs` (all overlapping hits, any
  frame) and `max_homopolymer` (longest one-base run). 0 = clean. DNA or RNA.
- repeat_score: `min_length` (default 8, >=2, only changes repeat_fraction). Columns max_repeat
  (AAAA is 3), max_inverted_repeat (self-complementary counts: ACGT is 4), repeat_fraction. All
  lower better. DNA or RNA.
- constraint_check: `reference` (required Dna, part of the hash), `targeted_codons` (default none).
  Columns protein_unchanged and length_unchanged (1.0 good, at_least 1.0), targets_remaining
  (in-frame count, at_most 0), targets_unreachable (information, not a gate).

| node | stop codon | trailing partial codon | empty sequence |
|---|---|---|---|
| gc_content | bases count | bases count | all 0.0, gc_deviation = target |
| codon_count | counted only if listed | never matches | 0.0 |
| codon_adaptation | skipped | skipped | 0.0 |
| codon_pair_score | its pair is scored | dropped | 0.0 |
| dinucleotide_bias, motif_count, repeat_score | bases count | bases count | all 0.0 |
| constraint_check | reads as `*` | ignored by protein_unchanged | 0.0 vs a non-empty reference |

Lowercase never reaches a node: Dna refuses it ("Not A, C, G, T"); inputs read from the goal are
upper-cased as they are added. In configs only codon_count's `codons` is upper-cased; every other
table or list refuses lowercase.

## Assertions
- Measure only: `produced` on the scorer step.
- Protein or length kept: `yes` on the at_least 1.0 filter over that constraint_check column.
- A codon, site or depletion bar: `yes` on an at_most filter over count, motifs, max_homopolymer
  or odds_ratio.
- Better than the first input: `yes` on beats_reference. higher=True where the goal raises the
  column (cai, codon_pair, a count it wants more of); higher=False where it lowers it (removed
  codons, motifs, repeats, odds_ratio, gc_deviation). gc has no better side; the goal's word sets
  it.

## Pitfalls
- Baseline and candidates must use one registered node: any field change (window, target, motif
  order, table) is a new hash and the filter errors "has score columns [...] but not [...]".
  Register the scorer once and use that id for both the baseline step and the candidate step.
- A tool step (generator, converter) drops every column: scorer, generator, then a filter on the
  old column errors with `score columns []`. Score again after the generator.
- CAI 0.0 hides two things. Nothing scorable: empty, stop only, amino acids the table lacks,
  or an empty table. Or one sense codon of a covered amino acid with weight 0 or missing, which
  zeroes the whole sequence, so a table naming only the input's codons scores every other
  synonym 0.0. Amino acids the table lacks are skipped silently. Copy the whole table the goal
  gives.
- gc_deviation is a subtraction: a window exactly on the bar can land above it (window 10, target
  0.45, a 30% window gives 0.15000000000000002, so bar 0.15 rejects it while the range 0.3 to 0.6
  passes). For a stated range use gc_min at_least lo, then gc_max at_most hi, with the goal's own
  numbers. A goal's 40% is 0.4.
- beats_reference is strict and integer scores tie: a baseline of 0 motifs or 0 codons cannot be
  beaten (10 candidates, 0 kept), and a tie goes to `.no`. "No worse than" has no
  filter; if the goal says it, state in the hypothesis what stays unchecked.
- Frame: codon_count is in frame; motif_count, dinucleotide_bias, repeat_score and gc_content read
  every position, so a site split across codons counts (ATGGT|CTCAA has one GGTCTC).
  Goal says codon -> codon_count; says site or motif -> motif_count.
- dinucleotide_bias: adding only A or T bases keeps the CG count, halves `frequency` and doubles
  `odds_ratio`. Use the column the wording names. A sequence lacking a base scores 0.0,
  which reads as fully depleted.
- repeat_score: random DNA scored 4-12 on max_repeat and 4-16 on max_inverted_repeat at 100-900 bp
  (20 each), so a bar of 0 is unreachable; take the bar from the goal or a baseline.
  repeat_fraction stays 0 for repeats shorter than min_length.
- constraint_check holds one reference for the whole list. Candidates from another input with a
  different protein get 0.0 on protein_unchanged and length_unchanged. Use one check per
  reference; a single one serves a pool only when every input encodes the same protein.
- A targeted codon with no untargeted synonym (ATG, TGG, or every codon of an amino acid targeted)
  keeps targets_remaining above 0, so at_most 0 keeps nothing. Target removable codons only.
- protein_unchanged translates both sides and reads every stop as `*`; it also fails when the
  first ATG changes (Met has one codon). length_unchanged only compares lengths. No column is
  positional, so none checks a goal about one position.
- The plan check refuses a plan with no generation step when the goal text holds higher, lower,
  reduce, decrease, increase, fewer, less, more, optimize, better, beat, minimize or maximize, as
  substrings ("unless" matches), and the goal has one input sequence. Rewording the hypothesis
  does not help; such a goal needs a generation step even to measure.
- RNA input: codon_count, codon_adaptation, codon_pair_score and constraint_check take Dna only
  ("takes Dna, but 'r' gives Rna"); the other four take RNA and read U as T.

## Not here
- Making the change these scorers check: sequence-recoding. RNA to DNA, translation:
  sequence-conversion.
- 5' structure, folding energy, translation initiation: expression-and-folding.
- Top-k, Pareto, several columns at once: screening-and-ranking. Assertion wording:
  criteria-and-assertions.
- Third-position GC, a codon at an index, lineage from a variant to its source: no node does these.
