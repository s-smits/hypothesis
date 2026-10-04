---
name: sequence-recoding
description: Making variants of a coding sequence that keep its protein, with codon_optimise, domesticate, gc_target_recode, mutate_synonymous, resample_synonymous, recode_targeted and protein_to_dna: which one a goal's wording calls for, config, pool size, stop and start codons, shapes, assertions.
---
# Recoding a coding sequence without changing its protein

(ran) marks behaviour seen running the node offline on small inputs; the rest is read from code.

## When to use
Pick by what the goal names, not by its verb.
- "optimise, raise CAI, follow this codon table": codon_optimise most_frequent. "Deoptimise,
  rarest codons": least_frequent. "Harmonise": weighted_sample. Needs the goal's table;
  without one nothing real fills `codon_weights` (say so in the hypothesis).
- "Remove restriction site, recognition sequence, forbidden motif": domesticate. "Remove or free
  a codon, no TCG codon": recode_targeted. The noun decides: a codon is in frame only, a site is
  read in every frame (ran: a TCG split across codons stayed under recode_targeted, domesticate
  removed it).
- "GC target, flatten GC-rich or AT-rich windows": gc_target_recode.
- "Lower, raise, find, improve X, keep the protein" where no table or list can aim at X (atoms,
  expression, folding): a mutate_synonymous pool. resample_synonymous for "random baseline".
- "Make N variants, diversify": mutate or resample with `variants_per_sequence`.
- "Design a gene for this protein": protein_to_dna (amino acids in, DNA out, needs a table).
- "Keep the start, keep this stop, leave codon i alone": no field does it. All seven keep an ATG
  at index 0; see Protein, start, stop and Assertions for the rest.
Look-alikes: codon_optimise aims at a table, recode_targeted at a codon list, domesticate at
motifs, gc_target_recode at windowed GC. mutate and resample aim nowhere, the pool is scored
afterwards: mutate = neighbours of the input (exactly `count` codons differ), resample = every
codon redrawn, so it can land on the input again (15 of 500 draws on a 5-codon toy).
Inputs are Dna; RNA or protein is refused ("takes Dna, but 's' gives Rna", ran): see
sequence-conversion. Only protein_to_dna takes amino acids.

## Shapes
- One recoder, bar from the goal: `seqs -> recoder -> constraint_check(reference=input) ->
  at_least protein_unchanged 1.0 -> scorer -> at_most|at_least`; add length_unchanged 1.0 only
  when the goal states a length. Scorer: motif_count `motifs` at_most 0 (site gone), codon_count
  `count` at_most 0 (codon gone), gc_content `gc_deviation` at_most (GC band). Scorers chain;
  columns stay on `.yes`.
- Beat the first input: `base = scorer(seqs)`; `seqs -> generator -> constraint_check -> gates ->
  scorer (same registered node as base) -> beats_reference(scored_in=base, reference=first input,
  higher=...)`. The baseline step must read the input and use the same node config, or the
  column differs. Compare with the first input or a number the goal gives, never with the input
  a variant came from: no node knows that.
- From a protein: `proteins -> protein_to_dna -> scorer` (ran: round-trips through dna_to_protein).
- Order of recoders (ran): the last one decides what holds. domesticate then mutate left a site in
  25 of 100 variants; mutate then domesticate left 0, 100 distinct. A deterministic recoder after
  a pool can collapse it: 100 resampled -> codon_optimise gave 1 entity (gc_target_recode and
  recode_targeted kept 100). Score every property.

## Config that decides the result
Plans may pass lists for tuple fields; a bare string is refused ("Input should be a valid tuple").
- mutate_synonymous: `seed` (required, no default), `count` (default 1, at least 1: codons swapped
  per variant, all swappable ones if fewer), `variants_per_sequence` (default 1). Each variant
  differs at exactly `count` codons (ran), none equal the input unless no codon has a synonym.
- resample_synonymous: `seed` (required), `variants_per_sequence` (default 1).
- codon_optimise: `codon_weights` (required; upper-case DNA codon keys, UUU, lower case and
  negative weights are refused), `strategy` (default most_frequent), `seed` (weighted_sample
  only). One output per input; `variants_per_sequence` is refused (ran). A codon the table lacks
  weighs 0: most_frequent never picks it, least_frequent picks it first (12 of 12 such codons,
  ran), so give the whole table. Synonyms that all weigh the same, or an empty table, leave the
  codon alone; most_frequent on an optimal input changes nothing.
- protein_to_dna: same three fields. Equal weights give the first codon in code order (TTT for F).
  `*` becomes the table's stop codon (TAA when tied); adds no ATG and no stop.
- domesticate: `motifs` (required, upper-case ACGT, 2 or more bases), `both_strands` (default True,
  also the reverse complement; give motif_count the same), `strategy` first|random (default
  first), `seed` (random only). Edits only codons overlapping a hit; a clean input comes back
  unchanged. Best effort: ATGATG stayed (ran). Count what remains with motif_count.
- gc_target_recode: `target` (required, a fraction: 50 refused), `window` (required, bases),
  `max_passes` (default 10). No seed, deterministic, one output per input.
- recode_targeted: `targeted_codons` (required, upper-case triplets, no repeats), `strategy`
  random|first (default random), `seed`. In frame only. ATG, TGG, or a codon whose synonyms are
  all targeted stay in place (ran); constraint_check counts them in `targets_remaining`.
- No node has a field for protected positions, fixed codons or a fixed region.
- Seeds: every draw is seeded by (seed, sequence), so a result does not depend on the batch and
  was identical across processes (ran). The same config and inputs reuse the cached result: to
  change a pool, change `seed`. A larger `variants_per_sequence` keeps the first N (ran).

## Pool size and duplicates
Entities with the same sequence are one, so a pool is smaller than inputs x variants_per_sequence
(asking hundreds per input kept well under that in recorded rounds). Size it from the input:
mutate at count 1 can make the sum over non-stop codons of (synonyms - 1) distinct variants;
resample the product of synonym counts (L, S, R 6; A, G, P, T, V 4; I 3; M, W 1; the rest 2).
The toy ATGGCTCTGAAATAA has 9 and 48: 500 requested gave 9 and 48 (ran). When distinct <
requested the space is spent: a new seed adds nothing, raise `count` or use resample.
Distance matters (ran): for an input already well adapted to a table, count 30 and resample put 0
of 100 above it, count 1 put 31. Keep `count` low and the pool large when the input scores well.

## Protein, start, stop, length (ran: 150 random CDS x 10 configs, partial tail, internal stop)
- Protein and length never changed. A trailing partial codon is left alone.
- ATG at index 0 never changed (Met has one codon). A first CTG, GTG or TTG is an ordinary codon
  and does change (CTG -> CTT under codon_optimise; resample changed it in 164 of 200).
- Stop codon (ran; what the nodes do now, a later change may alter some of them): mutate and
  resample never change it. codon_optimise replaces it when the table ranks another stop higher
  (TGA -> TAA) and keeps it when the stop weights tie or are absent; gc_target_recode when that
  moves window GC; domesticate when a hit overlaps it and no earlier codon can fix it, or under
  `random` (2 of 8 seeds); recode_targeted only if the stop is targeted. constraint_check keeps
  protein_unchanged 1.0 after any swap (stops read as `*`).
- Time (ran): all but gc_target_recode took under 1 s for 500 variants of 9 kb; gc_target_recode
  0.4 s per 900 nt, 2 s per 3 kb, 11 s per 9 kb (window 50). A step gets 5 minutes for the whole
  list (read from the code): keep sequences x seconds below 300.

## Assertions
- Protein kept: `yes` on the at_least 1.0 filter over protein_unchanged; `produced` on the
  generator does not cover it. Length: the same on length_unchanged, only if the goal states it.
  `produced` covers an alphabet criterion (typed) and, on protein_to_dna, that its output is the
  conversion of its input; not length, start or stop codon.
- Site, codon or GC band met: `yes` on the at_most filter, bar from the goal. One output per input
  passes or fails together, so `yes` fits the single recoders.
- Beats the first input: `yes` on beats_reference for one candidate per input; a pool splits, so
  `yes` fails (criteria-and-assertions: asserting on a split). `produced` alone for "excludes the
  ones below the baseline" was vetoed in a recorded round.
- No score is about: the start codon, the stop codon, a codon at index i (codon_count counts a
  codon anywhere), a region kept, "differs from its input", "N distinct variants", another ORF.
  Assert every other criterion; on the generator put `produced` and write in the hypothesis what
  the node does by construction and that nothing measured it. Expect the verifier to leave a
  criterion naming one of these uncovered. Do not stand length_unchanged in for a codon, and do
  not write that codon_optimise keeps the stop: it swaps it when the table ranks another higher.

## Pitfalls
- Leader before the ORF: codons are read from base 1. A 16 nt leader shifted the frame: the ORF's
  protein changed in 20 of 20 variants while constraint_check gave protein_unchanged 1.0 for all
  (ran). trim_to_first_start first; it cuts at the FIRST ATG in any frame, which can sit in the
  leader, and drops sequences without an ATG. The same holds for a second ORF in another frame.
- Several genes in one list: constraint_check and beats_reference hold one reference, so another
  gene's variants fail protein_unchanged (ran). See sequence-scoring, screening-and-ranking.
- A targeted codon that cannot go (ATG, TGG) keeps targets_remaining above 0: target removable
  codons. A targeted stop is swapped for another stop (TAG targeted -> TAA, ran).
- gc_target_recode is a local optimum, not the target (ran): worst-window deviation 0.04 to 0.1
  at windows of 50 and 100, and a target the protein cannot reach stops at its limit.
  max_passes 1 left 0.18 where 10 left 0.06. Give gc_content the same `window` and `target`;
  do not promise a band tighter than the deviation it measures.
- Already-optimal input: codon_optimise returns it, so beats_reference keeps nothing. A site-free
  input comes back from domesticate unchanged and `at_most 0` holds trivially.

## Not here
- Scorers and their columns: sequence-scoring. RNA to DNA, translation, leaders:
  sequence-conversion. Filters, top_k, ranking a pool: screening-and-ranking. Criteria and
  branches: criteria-and-assertions. Protein variants: protein-and-structure.
- No node: protect a position or region, recode a second reading frame, count variants, compare
  a variant with the input it came from.
