# Research directions

## Aim and evidence

The proposed question is whether an autoresearch system can discover better
**algorithms for synonymous recoding**, with improvements that transfer to unseen
genes. Improving individual DNA sequences, composing a better DAG, and inventing
new node implementations are distinct levels of capability. Report which level
an experiment actually exercises.

This note synthesises the supplied **Research Proposal.md** ("Genome Recoding as
an Autoresearch Testbed") and **work_breakdown.md** ("Five-Person Work Breakdown").
It checks their engineering assumptions against `f9d2f50` on `main`. The breakdown
was written against `a10526e`; its referenced `codebase_assessment.md`,
`architecture.md` and `track1_build_spec.md` were not supplied or present in this
checkout. Their details, including report-card schemas and hidden-term definitions,
remain unresolved. This note records a proposed direction rather than a completed
experiment or an instruction to implement every option.

For the tentative orchestration and agent structure, including the human-added
tool route in the supplied diagrams, see
[intended-structure.md](intended-structure.md). Its per-hypothesis retry loop and
the algorithm-selection loop below have different responsibilities.

## What exists and what changed

| Area | Present in this checkout | Consequence for planning |
| --- | --- | --- |
| Typed execution | Validated DAGs, list-based entities/tables, tools/scorers/filters, deterministic mutation, cached Temporal activities | Extend existing contracts rather than replacing the engine. |
| Builder and verifier | Configuration registry, schema inspection, bounded output retries, saved hypotheses and an LLM goal verdict | Reuse the builder; statistical evaluation is still separate work. |
| Biological prediction | `ostir_expression` with configurable UTR/anti-SD, OSTIR and ViennaRNA dependencies | Exercise and profile it before planning a new folding installation or proxy. |
| Examples | Two DNA workflow files and one DNA hypothesis file | The breakdown's obsolete arithmetic-example diagnosis has already been addressed. |
| Reporting | Goal → hypothesis → DAG/outcome/verdict pages and a run-progress view | Add trajectory and quantitative evidence to this surface when experiments exist. |
| Missing research machinery | No search package, instance benchmark, code authoring, sandbox, learned oracle, statistical comparison, baselines or attempt ledger | A working demo is not yet evidence of autonomous scientific improvement. |

Tables already carry lists and per-entity scores. Start by evaluating fixed
instances through those contracts; introduce `DnaSet`/`ScoreSet` only if a concrete
collection operation cannot be represented cleanly. Sequence IDs change under
mutation and identical sequences merge, so instance identity and lineage need a
separate mapping regardless of whether a collection type is added.

There is small documentation debt: README's final command block lacks a closing
fence in the assessed base, and the hypotheses page mentions a missing
`examples/score.json`. The fence is repaired with this documentation change; the UI
example reference belongs in a subsequent UI fix. Validate examples again after
any node-version or schema change.

## Possible directions

| Direction | Question and smallest useful result | Dependencies and trade-off |
| --- | --- | --- |
| Benchmark first | Can a deterministic evaluator distinguish recoding strategies? Build a fixed small instance set, hard gates, per-instance metrics and random/greedy baselines. | Lowest-cost way to make later scores interpretable. It establishes evaluation, with limited autonomy. |
| Search over existing nodes | Can failure-guided DAG composition outperform equal-budget sampling? Add a simple parent → child loop, ledger and frozen final evaluation. | Reuses `build_agent`; tests strategy search while keeping the node library fixed. It may be constrained by the current nodes. |
| Node-authoring autoresearch | Can the system invent a reusable recoding operation and later select it independently? Demonstrate authored-node reuse, improvement and ablation. | Stronger novelty claim, requiring isolated validation/execution, versioned library loading and more compute. Begin after the benchmark and loop work. |
| Biology-led predictive benchmark | Can strategies improve independently assessed biological proxies or predictions from experimental data? Validate dataset suitability and a separate evaluator. | Stronger external relevance, but data mismatch, leakage and oracle exploitation can dominate the result. Predictive improvement still needs careful qualification. |
| General framework / second domain | Does the same search protocol help outside recoding? Add a small deterministic second domain behind the same interfaces. | Tests transfer of the framework. A toy arithmetic domain proves integration; a nontrivial task with baselines is needed for a search-quality claim. |

Recommended starting point: **benchmark first, then search over existing nodes**.
This yields an interpretable result even if code authoring or external data is
delayed. Keep node authoring as the next experiment if the available node library
is demonstrably the limiting factor. Keep the candidate algorithm and evaluation
system separate from the beginning so later autonomy does not invalidate the test.

## A staged experiment

### 1. Define and freeze the benchmark contract

Choose the organism and genetic code, exact target codons, dataset accession and
version, inclusion/exclusion rules, immutable genomic context, metric directions,
split seed, budget and success criteria. The breakdown proposes NC_000913.3,
100 CDS with flanks and a 50-gene development evaluation. Treat these as starting
sizes; confirm annotation quality, expression metadata and runtime first.
Stratification by expression decile needs an identified expression dataset.

Keep CDS and flanks separate and document strand handling. Decide how overlapping
CDS, alternative starts, incomplete annotations and regulatory context affect
eligibility. Protein preservation in one reading frame does not establish
preservation of other genomic functions. Start with explicitly eligible genes
and report the exclusions.

The suggested five interfaces are useful integration seams, but their exact
types should follow the evaluator's needs:

- `Instance` and `load_instances(split)`: stable instance IDs, original CDS,
  context, target codons and dataset/split provenance.
- `score_dag(dag, instances)`: per-instance metrics, constraint status, failures
  and aggregate diagnostics. A bare `dict[str, float]` cannot explain dropped
  genes, invalid outputs or trade-offs between objectives.
- Parent/child comparison: paired effects, uncertainty and a typed quantitative
  verdict, separate from the existing goal-verifier verdict.
- Node authoring, if selected: a validated artifact with implementation/config
  versions and validation receipts, or a recorded failure.
- An append-only attempt record: IDs, parent, proposal, DAG, budgets, outputs,
  metrics, failures and the reason for selection or rejection.

Stubs may unblock integration, but synthetic scores must be labelled and absent
from research claims. Freeze signatures before concurrent implementation and
change the owning schema, callers and stubs together.

### 2. Establish interpretable baseline scores

Hard gates check identical translated protein, unchanged CDS length and complete
elimination of target codons. Log violations; do not let a soft objective
compensate for them. The existing mutation node preserves protein but randomly
changes eligible positions; it does not enforce target-codon elimination.

Use atom count for a quick execution check. It is additive across bases, so a
per-codon choice solves the unconstrained synonymous optimum; improvements here
give little evidence of discovered biological design principles.

OSTIR is an available starting proxy. Decide whether to maximise predicted
initiation or minimise deviation from each original sequence: the two can favour
different designs. Keep the reference context fixed and record the UTR and
ribosome parameters. Later objectives might cover codon usage, local folding,
motifs and codon-pair effects, provided their definitions and provenance are fixed.
Retain component scores rather than hiding all trade-offs in one number.

Run random synonymous recoding, greedy recoding and best-of-N under declared,
comparable budgets. Report evaluations, model tokens/cost and wall-clock time
separately. Profile a small real batch before adopting the breakdown's target of
under five seconds for 50 genes. Record cache state and native-library versions.

### 3. Close one search loop before enriching selection

Implement sample → propose a hypothesis/DAG → validate → execute → evaluate →
compare → record → select, using the existing builder and typed DAG validation.
Require bounded generation/retry/cost budgets, recorded failures and restart from
persisted attempts. Begin with one population and a transparent selection rule.
Add islands, migration or semantic memory only after measuring stagnation or
repeated proposals. The proposed four islands, 20-generation migration interval
and 0.15 weak-parent probability are tunable suggestions, not requirements.

Feed the model development metrics, representative failures and prior attempts.
Store exact attempt evidence even if the prompt contains a compressed summary.
Describe a statistically inconclusive result as inconclusive; do not convert every
unselected child into a "refuted" scientific hypothesis.

A local topological runner can reduce Temporal overhead for many small benchmark
runs if profiling supports it. Preserve the same output, cache, alignment,
deduplication, score propagation and empty-branch behaviour; verify equivalence on
representative DAGs. Keep Temporal for resumable workflows and the existing demo.
Do not make a new executor an obligatory rewrite of working infrastructure.

### 4. Add authoring only behind a real isolation boundary

Manual `factory.py` registration is simple today. Auto-discovery becomes useful
when concurrent node additions or runtime authoring justify it; keep duplicate
names, import failures, schemas and load/restart behaviour explicit. Blindly
scanning/importing generated Python would execute unvalidated code.

Generate into an isolated staging area, validate imports and schema/signature
contracts there, smoke-run on development fixtures, then publish a versioned
artifact. Keep quarantine outside the discovered library. Pin the allowed
dependencies and capabilities; code should not see secrets, write the evaluator
or read held-out data. Resource limits and timeouts do not supply those access
controls by themselves.

An authored node used by a later generation is a useful novelty demonstration.
To claim it helps, freeze the chosen algorithm and ablate that node under the same
evaluation protocol. Code creation alone does not establish scientific discovery.

### 5. Confirm and report the result

Separate adaptive development selection from frozen confirmation. The breakdown
suggests paired Wilcoxon tests, a paired bootstrap and a run-wide
Benjamini–Hochberg correction. Finalise the statistical protocol before seeing
confirmation outcomes, including handling ties, zeros, dependence and the family
of comparisons. Corrections do not make reused development data independent.

Split related sequences carefully if training an external oracle. Record its own
validation performance, feature definitions and dataset provenance. Separate
oracle training/validation, algorithm development and final testing. Two models
trained on disjoint subsets of the same assay may still share biases; transfer
between them is useful evidence with that limitation stated.

The breakdown's three held-out peeks should be understood as a logged access
budget, not a guarantee of an untouched test set. Prefer one final evaluation
after selection is frozen. If earlier peeks guide changes, reserve a separate
unseen confirmation set or report the leakage explicitly. Keep held-out feedback
out of candidate prompts, caches accessible to candidates and failure summaries.

Produce a manifest, per-instance results, baseline comparisons, budget/failure
accounting, trajectory and ablations. Define the report-card schema from those
artifacts so every figure traces to a reproducible calculation. Extend the
existing hypotheses UI with parent/generation links, quantitative effects and
uncertainty, budget counts and authored-node markers when that data exists.

## Decisions still needed

- **Claim and scope:** choose the first direction and whether the target is a
  short demonstration or a research result. Record the available time/compute
  budget; do not assume README's credit amounts remain available.
- **Data:** identify the precise Cambray paper/supplement intended by the breakdown,
  its licence, assay, context and applicability before building an oracle. On
  unsuitable data, a predeclared hidden-objective experiment is a narrower
  computational test. Its exact protocol is still to be defined.
- **Objective:** specify which codons are removed and whether to preserve or
  improve each proxy; avoid silently equating predicted initiation with fitness.
- **Isolation:** choose the execution boundary and allowed capabilities before
  enabling model-authored code. Native dependencies may influence that choice.
- **Evidence:** predeclare splits, budgets, independent confirmation and the
  threshold for a meaningful improvement. Document negative or inconclusive
  findings with the same care as a positive result.

For a team, the five suggested responsibilities remain sensible: search and
ledger; execution and authoring; instances and biological metrics; statistics and
baselines; UI and reproducibility. Assign shared-contract ownership explicitly.
Use executable gates (synthetic integration, first real paired evaluation, frozen
run, reproducible report) instead of relying on the original event-hour schedule.

Defer multi-machine storage, a second reporting frontend, a full-genome challenge
and wet-lab work until a smaller computational result warrants them. Arithmetic
can be restored from history after checking compatibility with today's sequence
types, rather than applying an old checkout command blindly. Bin packing is an
alternative with a clearer optimisation comparison, at greater implementation
cost. Neither second domain should delay the first sound recoding experiment.
