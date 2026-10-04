---
name: hypothesis-loop
description: Start here. Routes a biology goal to the skill for its task family, says what the loop's stages and limits mean for planning, and lists what no node can do so a plan does not chase it.
---
# The hypothesis loop: where to start

## When to use
Before planning any goal. The nodes are the only tools: a plan composes the ones that exist (conversion,
scoring, filter, generation), and none can be requested. The skills below say how to do each kind of task well
with them. Read the one that matches, plus `criteria-and-assertions` before the first plan.

## Which skill
- Convert or re-read a sequence (RNA to protein, the other strand, a transcript, protein to a DNA that encodes
  it, cut to the first ATG), or bridge a port's kind: `sequence-conversion`.
- Measure composition or codon use (GC, codon counts, CAI, codon pairs, CpG, motifs, repeats, fixed constraints
  after a recoding): `sequence-scoring`.
- Translation initiation, ribosome binding, 5' mRNA structure, folding energy: `expression-and-folding`.
- Choose, screen, rank, "best N", beat a baseline, trade off two measures: `screening-and-ranking`.
- Change a coding sequence and keep its protein (optimise, remove a site, hit a GC target, diversify, N
  variants): `sequence-recoding`.
- Fold, repair a structure, contacts across chains, protein variant libraries: `protein-and-structure`.
- Every goal: `criteria-and-assertions` (what each assertion proves, which claims no node can hold).
- Round two and later: `diagnosing-a-failed-round`.
A goal often needs two: improving expression is `sequence-recoding` for the pool, `expression-and-folding` for the
score and `screening-and-ranking` for the bar.

## What the loop does with a plan
- Criteria are fixed first, from what the goal states. The builder plans a DAG and an assertion for each
  criterion. The DAG runs. A verifier judges the outcome and can veto but never grant. A critic explains a miss
  and the builder plans again.
- A run is capped at 20 rounds and 500,000 tokens, and the token cap is checked only before a round, so a
  run gets four to six rounds, each costing on the order of 100,000 tokens. Spend the first plan on being right,
  not on exploring.
- A plan must differ from every earlier one in wiring. Reworded prose is not a change.

## What no node can do
Do not plan toward these. Assert what the nodes can show and say in the hypothesis what stays unchecked.
- Sequences: cut at the first stop or drop a terminal stop, read a fixed frame offset, add a start or stop
  codon, take an ambiguity code, use another genetic code, score a protein string directly.
- Codons: say which stop codon a sequence ends in, read a codon at an index, protect a position or a region
  while recoding, recode a second reading frame.
- Comparison: pair a variant with the input it came from, count the entities in a set, take a weighted sum of
  columns, assert a margin.
- Expression: design or vary a UTR or RBS, find the true start of translation, predict half-life or yield,
  rank by distance to a target value.
- Protein: score a structure (confidence, stability, binding energy, clashes), compute a protein property
  (mass, pI, hydrophobicity), count or compare mutations between proteins, list ligand or DNA contacts.

## Before you plan
1. Name the goal's inputs exactly as given, and check every node's port accepts that input's kind.
2. Decide what each criterion is checked by, in the node and branch that can hold it, before wiring
   (`criteria-and-assertions`).
3. If a step must be a node no skill covers, look it up with `search_nodes` and read `describe_node`: a node's
   config often covers a case its summary does not name.

## Not here
Node-by-node behaviour, config and columns: the skill for the family. The nodes' own descriptions:
`describe_node`.
