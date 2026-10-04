---
name: protein-and-structure
description: Protein-side goals - fold sequences alone or as one complex (esmfold2_fold), repair a structure (pdbfixer_fix), list residue contacts across chains (chain_contacts), design variant libraries (protlib_design), and cross between protein and DNA.
---
# Protein and structure

Marks: (ran) executed offline on small synthetic mmCIF files; (stub) the Modal call replaced by a
stand-in; (read) code only. esmfold2_fold and protlib_design need a Modal GPU: folding and scoring
are read, not run.

## When to use
- "fold", "predict the 3D structure": esmfold2_fold. Several proteins "together", "as a complex", "the
  interface between them": esmfold2_fold with as_complex true.
- "repair", "clean", "add hydrogens or missing atoms", "remove water or ligands", "non-standard
  residues": pdbfixer_fix. "Which residues touch across chains", "interface", "contact distances":
  chain_contacts. "A library or panel of variants", "mutations at positions": protlib_design.
- Protein to a DNA scorer, or a gene to a protein node: protein_to_dna, dna_to_protein (details in
  sequence-conversion).
- Kinds (ran). amino_acid_sequence and a structure's `sequence` hold the 20 letters and `*` only; X,
  U, B and lower case are refused when the entity is made. protein_structure also holds `structure`,
  an mmCIF string not checked on construction (any text is accepted, the node fails later); its id
  hashes both, and `sequence` is never checked against it: chain_contacts copies it, protlib_design
  indexes positions by it, pdbfixer_fix rebuilds it.
  protein_contacts holds `contacts` (residue_a, residue_b, distance in A, closest first; a residue
  reads `H:SER:10:`, chain:name:number:insertion code), shown as "22 contacts; closest ... at 2.76 A".
- The builder is shown a structure input as kind, count and `sequences` only (ran): no chain ids, chain
  count, numbering or coordinates. Take chain ids from the goal's words; with none, leave them empty.
- No scorer takes amino_acid_sequence, protein_structure or protein_contacts (every scorer takes dna
  or nucleic_acid), and the six nodes here make entities, no score column (ran, registry). A filter on
  their output is refused: "filters on 'x', but 'contacts' has score columns []" (ran).

## Shapes
- Fold this: `seq -> esmfold2_fold` (from DNA: `dna -> dna_to_protein -> esmfold2_fold`): one monomer
  per sequence, the mmCIF in `.structure`.
- Fold chains together, then the interface: `chains -> esmfold2_fold{as_complex:true} ->
  chain_contacts{chains_a:["A"], chains_b:["B"]}`. `chains` is one input holding two or more different
  proteins, lettered A, B, ... in its order (stub; lettering read). A port reads one source, so chains
  given as separate inputs cannot be folded together (read). A homodimer cannot be asked for.
- Fix a given structure: `structure -> pdbfixer_fix{seed:1}`. Fold then prepare: `seq -> esmfold2_fold ->
  pdbfixer_fix`. "Fix then fold" does not wire: esmfold2_fold takes an amino_acid_sequence and no node
  turns a structure into one (the plan check refuses it, ran). Fold only sequences given as sequences.
- Residues touching across an interface of a given structure: `structure -> chain_contacts{cutoff,
  scheme, chains_a, chains_b}`; fix first only if the goal asks for the repair.
- Design variants: `structure -> protlib_design{positions}`. Given only a sequence: `seq ->
  esmfold2_fold -> protlib_design{positions:["*A*"]}` (a monomer is chain A).
- Design, keep by a gene-level property, fold the keepers: `structure -> protlib_design ->
  protein_to_dna{codon_weights} -> <DNA scorer> -> at_least|at_most|top_k -> <filter>.yes ->
  dna_to_protein -> esmfold2_fold` (the first four steps wired and passed the plan check, ran).

## Config that decides the result
- esmfold2_fold.as_complex (false): false folds each sequence alone on ESMFold2-Fast; true folds every
  sequence that arrives as ONE complex on ESMFold2 and returns one structure whose `sequence` is the
  chains joined in arrival order (stub). Identical sequences merge before the step (ran): no
  homodimer. Protein chains only. num_loops 3, num_sampling_steps 50, seed 0: leave unless asked. A
  trailing `*` is dropped from each chain; a `*` inside, or only `*`, raises (ran).
- chain_contacts.cutoff (5.0): the goal's distance in A, applied to the scheme's own distance.
  scheme: closest-heavy (default; non-hydrogen atoms), closest (hydrogens too), ca (alpha carbons;
  longer than closest-heavy for the same pair, so a Calpha goal needs the cutoff it names; ran).
  chains_a / chains_b are author chain ids: empty chains_a = every chain pair; chains_a alone = against
  all others; residue_a is on the chains_a side. chains_b without chains_a, or one chain on both
  sides, is refused (ran). A chain with no protein residues (water, ion) or absent: an error naming
  the chains the structure has (ran). Waters, ligands and unknown residues are skipped; MSE and SEP
  count as protein (ran).
- pdbfixer_fix runs: replace_nonstandard (true; MSE to MET), remove_heterogens (none | keep_water | all),
  add_missing_residues (false), add_missing_atoms (true), ph (7.0 adds hydrogens; null adds none), seed.
  remove_heterogens: keep_water drops ligands and ions and keeps water; all drops water too; no option
  drops water alone (ran). "No hydrogens" is ph null.
- protlib_design.positions (required): `WA12` (the residue there must be W, chain A, 1-based into
  structure.sequence), `*A*` (whole sequence), `*A{3-20}` (inclusive). One chain letter across all
  entries. library_size 10, min_mut 1, max_mut 4 set the library; forbidden_aa (letters no variant may
  gain) and max_arom_per_seq (cap on F, Y, W) carry goal constraints. use_ifold true (ProteinMPNN on the
  structure), plm_models ["facebook/esm2_t6_8M_UR50D"]; both off is refused (ran). schedule 1 or 2
  needs schedule_param of two integers (ran). Leave the other fields unless the goal names them.
- protein_to_dna.codon_weights is required, `{}` is allowed; dna_to_protein has no config: see
  sequence-conversion. protein_to_dna writes `*` as a stop and adds none; dna_to_protein keeps `*`.

## Assertions
- A shape with no filter: assert produced on the last step. It holds with zero contacts (ran): it
  shows a result exists, not what it holds.
- esmfold2_fold (sequence to structure) and chain_contacts (structure to contacts) change the kind, so
  the verifier may count produced as covering "the structure is the fold of the input" or "the contacts
  across the chains are listed"; whether it does for a prediction is a model call (read, not run).
  pdbfixer_fix keeps the kind and protlib_design is a generation step: produced covers only that an
  output exists. None of them covers what it holds: how many contacts, which pair, how good a fold.
- A criterion on a contact count, a named residue pair, structure quality (confidence, clashes, energy),
  binding, or how far a variant is from the original: no node measures it. Assert produced and say in
  `hypothesis` what is unchecked. Bound mutations by min_mut, max_mut and positions, and say it is by
  configuration.
- After protein_to_dna and a DNA scorer, assert yes on the filter (screening-and-ranking).

## Pitfalls
- Contacts after pdbfixer_fix use other chain ids. OpenMM reads chain ids from whichever of
  label_asym_id and auth_asym_id has more distinct values; a file with a water or ligand under its
  own label id (most deposited entries) has more label ids, so author chains H, L came out A, B, and
  chain_contacts{chains_a:["H"]} then raised (ran; without heterogens H, L stayed). Contact the
  given structure, or after a fix leave the chains empty.
- Hydrogens move contact counts: after ph 7 the count under scheme closest rose, while closest-heavy
  moved only by added heavy atoms (ran). Use closest-heavy, or ph null, to compare with the input.
- pdbfixer_fix seed 0 is not reproducible: three runs of one config gave three structures when atoms
  were added; seeds 1 and 2 repeated (ran). Set seed 1 or more.
- pdbfixer_fix rebuilds `sequence` from the residues present: standard names only, chains joined, `*`
  gone. With replace_nonstandard false the MSE residue vanished from it (ran). add_missing_residues
  added nothing when the file had no _entity_poly_seq (ran, silent); with it and _struct_asym the gap
  was filled (ran). Added atoms and residues are modelled coordinates.
- A structure given as PDB-format text, or any non-mmCIF text, fails in pdbfixer_fix and
  chain_contacts with `IndexError: list index out of range`; a multi-model file gives "Expected one
  model, found N" (ran). No node converts PDB text or picks a model: do not rewire around it.
- as_complex on a list of candidates returns one structure for the lot, not one each (stub). Do not
  use it to fold a protlib_design library.
- protlib_design on a complex (sequence = chains joined): `*A*` expanded over every residue and
  labelled all of them chain A, and each library member came back as the whole joined string (stub).
  Design the first chain with `*A{1-n}`, n its length; members carry the other chains unchanged, as one
  string that folds as one chain, not the complex. `*` in the structure's sequence, a wrong wild-type
  letter and a range outside the sequence are refused (stub). A given structure's chain letter must be
  the file's own: take it from the goal (read).
- protlib_design returns sequences only: its ProteinMPNN and language-model scores are not output, so
  nothing can rank or keep variants by them. A gene-level score on protein_to_dna output measures the
  codons that node chose as much as the amino acids, and beats_reference needs its reference to be an
  input entity (the plan check, read), which a back-translated wild type is not: use at_least,
  at_most or top_k with a number the goal gives.
- A measure-only goal on one structure that contains "more", "less", "lower", "better" and the like
  (substring match) is sent back until the retries run out: one input entity and no generation node
  (ran). Do not add protlib_design to pass it unless variants are asked.
- The verifier is sent its whole view (plan, inputs, outcome) as is only if it fits 150,000 characters;
  a structure costs about 130 characters per atom, so 60 residues with hydrogens plus its input table
  took 122,000 (ran). Past the limit it sees each entity as kind and sequence only: coordinates and
  contact lists vanish. Do not write `expected` that depends on it checking them.
- Nothing cuts a protein at its first stop, so a DNA with an in-frame stop before its end gives a
  protein esmfold2_fold refuses (ran): count stops first (sequence-conversion).

## Not here
- Translation frame, stop trimming, RNA, codon table for protein_to_dna: sequence-conversion.
- DNA scores for back-translated variants: sequence-scoring. Filters on them: screening-and-ranking.
- mRNA folding and translation initiation (not protein structure): expression-and-folding.
- Criterion wording and which branch covers it: criteria-and-assertions. A missed round:
  diagnosing-a-failed-round.
- No node can: score a structure (confidence, stability, binding energy, clashes, RMSD, secondary
  structure); count or compare mutations between proteins; compute a protein property (mass, pI,
  hydrophobicity); fold a homodimer or a protein-DNA or protein-ligand complex; list ligand or DNA
  contacts; read chains out of a structure; convert PDB to mmCIF; return protlib's predicted effects.
