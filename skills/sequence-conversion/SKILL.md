---
name: sequence-conversion
description: Convert or re-read sequences between DNA, RNA and protein and between strands (transcribe, translate, back-translate, complement, reverse complement, trim to first ATG), bridge port kinds, and check a result a goal states exactly.
---
# Sequence conversion

## When to use
- Goal wordings: translate or convert RNA/DNA to protein or amino acids; DNA that encodes a protein;
  the other, opposite, antisense or template strand; reverse complement; complement; transcribe,
  mRNA, transcript; start at the first ATG; keep proteins of at least N residues.
- A scorer, generator or folder whose port kind is not the kind you hold (see Shapes).
- Input kinds: dna, rna, amino_acid_sequence. Plan on the kind given, not the goal's word for it.

## Shapes
Ports: Dna (dna_complement, dna_reverse_complement, dna_transcribe, dna_to_protein,
trim_to_first_start, codon_*, constraint_check, every recoder and mutator), Rna (rna_back_transcribe),
AminoAcidSequence (protein_to_dna, esmfold2_fold). gc_content, motif_count, repeat_score,
dinucleotide_bias, mrna_fold_energy, mrna_5prime_mfe, ostir_expression take Dna or Rna and gave the
same scores for either (ran): do not convert RNA before them. A wrong kind is refused before the
run: "Step 's' port 'sequence' takes Dna, but 'rna_seq' gives Rna" (ran).
- RNA to protein: rna_back_transcribe -> dna_to_protein. DNA to protein: dna_to_protein.
- DNA to mRNA: dna_transcribe (coding strand in). Template strand in: dna_reverse_complement -> dna_transcribe.
- Other strand: dna_reverse_complement (read 5' to 3'). Complement in the same direction: dna_complement.
- RNA other strand: rna_back_transcribe -> dna_reverse_complement -> dna_transcribe (ran).
- Protein to DNA: protein_to_dna {codon_weights: {}}. Protein to RNA: add dna_transcribe.
- Protein to a DNA scorer: protein_to_dna -> scorer. DNA to a folder: dna_to_protein -> esmfold2_fold.
- Gene on the minus strand: dna_reverse_complement -> [trim_to_first_start] -> dna_to_protein.
- 5' leader before the ORF: trim_to_first_start, then the scorer or dna_to_protein.
- Keep proteins of at least N residues: codon_count {codons: the 61 sense codons} -> at_least
  {column: the count, threshold: N} -> dna_to_protein (ran: the count equals the residues without '*').
  From a protein input put protein_to_dna {codon_weights: {}} first (ran): the proteins come back whole.
- Check a protein the goal states: ... -> dna_to_protein -> protein_to_dna {codon_weights: {}} ->
  constraint_check {reference: {"sequence": DNA}} -> at_least {column: protein_unchanged's,
  threshold: 1.0} [-> at_least on length_unchanged's column, 1.0, reading the yes branch]. Reference =
  a DNA that encodes the protein the goal states, not the input (see Assertions).

## Config that decides the result
- dna_to_protein, dna_complement, dna_reverse_complement, dna_transcribe, rna_back_transcribe,
  trim_to_first_start have no fields. Any field (to_stop, table, stop_symbol, frame) is refused with
  "Extra inputs are not permitted" (ran). Translation is frame 1 from base 1, the standard code.
- protein_to_dna.codon_weights: required, no default; omitting it is refused ("Field required").
  {} is accepted and gives the first codon of each residue in TCAG order (M ATG, L TTA, F TTT, K AAA,
  * TAA), whatever the strategy (ran). That is a fixed choice, not an optimised one: never call it so.
  Keys must be upper-case DNA codons, weights not negative. A codon absent from a table weighs 0, so
  strategy least_frequent returns an unlisted codon: list all codons (ran). weighted_sample seeds per
  protein from `seed`. dna_to_protein(protein_to_dna(p)) gave p for all 20 residues and '*' (ran).
- trim_to_first_start: starts at the first ATG found in any frame, cuts the tail to whole codons,
  drops a sequence with no ATG (ran: 'GGATGAAATAA' gives 'ATGAAATAA', 'CATGCATG' gives 'ATGCAT').
- constraint_check.reference: one Dna for the whole input list. protein_unchanged is 1.0 when the
  translations are equal; length_unchanged compares base counts.
- codon_count.codons: counts in-frame codons from base 1. All 64 give the codon count; the 61 non-stop
  give the residue count without '*'; (TAA, TAG, TGA) give the stop count (ran).
- motif_count: motifs are A, C, G, T only, 2 bases or more, even for an RNA output (RNA is read as its
  DNA; a U motif is refused). Set both_strands false when the strand is what you check.

## What the nodes do with frames, starts, stops and partial codons (ran)
- Every in-frame stop becomes '*' and translation goes on: 'ATGTAAGGG' gives 'M*G'. A terminal stop
  stays: n codons give n characters, the last '*'. No node cuts at the first stop. Write `expected`
  that way.
- A partial tail is dropped with no error: 'ATGGCGTA' gives 'MA'. Frame 2 or 3 at a fixed offset has
  no node: only the first ATG can start a read elsewhere than base 1.
- After dna_reverse_complement -> trim_to_first_start -> dna_to_protein, bases past the stop are
  still translated: 'ATGGAAGGTTAAGGG' gives 'MEG*G'.
- protein_to_dna turns '*' into a stop codon, adds none, and adds no ATG: a protein without '*' or
  without a leading M gives a CDS without a stop or a start.

## Assertions
- A goal that only converts (translate, transcribe, complement, back-translate): the chain from
  Shapes with "produced" on its last step covers the criterion that the output is that conversion of
  the input, and nothing beyond it. Add no count, filter, trim or round trip to check it more: each
  passes the entity on unchanged and adds nothing.
- What a goal states beyond the conversion is not covered by "produced". The type allows '*' in a
  protein, so "no stop symbol" is not covered either. Measure it:
  - A protein the goal gives (exact string, first residue M, terminal stop, residue count with the
    stop): the check chain, yes on the protein_unchanged filter (ran: held for the right reference,
    not for a stop-free or one-residue-wrong one). Add length_unchanged for the base count. Against
    the input as reference it only repeats the conversion. Equal translations fix M first and '*' last,
    not which stop (a TGA-ended reference passed for a TAA input, ran): count stops on the DNA first.
  - Residues without the stop, or "at least N": codon_count on the 61 sense codons, then at_least or
    at_most; yes on the last filter. Stops: codon_count {TAA, TAG, TGA}; at_most 0 means none; at_most
    1 also passes with no stop at all (ran), and no count says where the stop sits.
  - An exact DNA or RNA output the goal gives: motif_count {motifs: (expected,), both_strands false}
    -> at_least 1. A motif shorter than the output still matches (ran): write it as long as the output.
    With both_strands true a motif and its reverse complement both count, so the strands cannot be
    told apart (ran: the input itself passed as the expected output of dna_reverse_complement).
- A batch: constraint_check serves one reference, so with two inputs the other fails the filter and
  yes is not held (ran). For many inputs assert what a shared count shows and say in `hypothesis`
  what is left unchecked.

## Pitfalls
- A goal that asks for the protein without its terminal stop cannot be met: dna_to_protein has no
  option and no node shortens a 3' end. domesticate, recode_targeted, codon_optimise,
  mutate_synonymous, resample_synonymous and trim_to_first_start each kept a stop codon on a
  stop-terminated CDS, at most swapping one stop for another (ran). Say in `hypothesis` that the output
  ends in '*', add no step that passes the entity on unchanged, and assert the stated criterion with the
  check chain against a stop-free reference: it fails (ran), and the failure is true. An instruction
  to set a stop or frame option on dna_to_protein cannot be followed: it has none.
- Outputs that are equal merge into one entity (ran: two synonymous DNAs gave one protein), and
  trim_to_first_start drops sequences with no ATG. Do not assert one output per input.
- trim_to_first_start in front of dna_to_protein changes nothing when the sequence begins with ATG
  and its length divides by three: leave it out unless a leader exists.
- Input that is not clean never reaches a node. The entity types refuse an N or any IUPAC code, a U
  in DNA, a T in RNA ("Not A, C, G, T" / "Not A, C, G, U"), X, B, U or lower case in protein; the
  inputs stage upper-cases and trims first, then asks the model to retry (ran). A length not
  divisible by three is accepted by every node here, never refused: see the partial tail above.

## Not here
- Scores of the converted sequence (GC, CAI, repeats, folding, expression): sequence-scoring,
  expression-and-folding. Synonymous recoding, stop codons that must stay: sequence-recoding.
- Structure from a protein, protein variants: protein-and-structure. Criteria wording and which
  branch covers it: criteria-and-assertions. A round that missed: diagnosing-a-failed-round.
- No node can: cut a sequence at its first stop or drop a terminal stop, read a fixed frame offset,
  add a start or stop codon, take an ambiguity code, use another genetic code, or score a protein
  string directly.
