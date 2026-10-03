# Working in hypothesis

## Purpose and scope

This repository composes typed, reproducible DAGs to explore a goal, runs them on
Temporal, and records an LLM verifier's judgement. The proposed research direction
is to improve the **recoding algorithm across genes**, using genome recoding as a
computational testbed. A better sequence for one gene is a different result from a
better generalisable design algorithm.

Read [README.md](README.md) for usage and
[docs/research-directions.md](docs/research-directions.md) for the proposal,
alternatives, priorities and unresolved decisions. Read
[docs/intended-structure.md](docs/intended-structure.md) for the supplied architecture
sketches, layer responsibilities and current-versus-proposed behaviour. These notes
are designs under discussion, not claims that their proposed components exist.
Follow the user's requested scope; do not implement the whole roadmap during an
unrelated fix.

## Start with evidence

1. Check `pwd`, `git status --short --branch` and `git worktree list`. Preserve other
   people's changes and processes. Fetch before using `origin/main` as current
   evidence for branch or PR work.
2. Read the relevant implementation and its tests before changing the contract.
   Treat old plans, README claims and model-generated assessments as hypotheses.
   Verify them against the checkout; amend stale guidance when the change affects it.
3. State the smallest observable outcome that would complete the task. Distinguish
   plumbing, predictive scores, statistical evidence and biological validation.
4. Reuse an existing node, type, runner or persistence helper before adding another.
   Prefer the standard library and installed dependencies. Add abstractions when a
   concrete second use needs them, and measure before optimising.

Write plain, measured prose with British spelling. Give evidence and limits rather
than promotional claims. Plans and performance targets are not measured results.

## Setup and checks

Python is `>=3.12,<3.13`; use `uv` and the committed `uv.lock`.

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

These are separate checks. Run the relevant tests for a code change, then the full
suite for changes to shared contracts or execution. Check lint, formatting and
types for Python edits; report existing failures separately instead of broadening
a narrow change to repair them. For prose-only changes, verify paths, commands and
the diff; a new test framework or documentation test suite is unnecessary.

Tests isolate `NODE_DAG_RESULTS` through the autouse fixture in
`tests/conftest.py`. Agent tests use scripted models. Temporal integration tests
start a time-skipping test server, which may download a binary on first use;
OSTIR tests exercise the installed ViennaRNA bindings. Classify missing binaries,
network failures and native dependencies separately from assertion failures. Never
silently skip a failing integration check and claim the full suite passed.

For manual execution, use separate terminals:

```sh
temporal server start-dev
uv run python -m temporal.run_worker
uv run python -m temporal.run_ui
uv run python -m temporal.run_workflow examples/simple.json
```

The default Temporal address is `localhost:7233`, queue `node-dag`, and UI
`http://127.0.0.1:8000`. Use CLI `--help` for overrides. Agent runs additionally
need `ANTHROPIC_API_KEY` and `--model`; a proposed model string must be checked
against the chosen provider. `temporal/run_hypothesis.py` loads the repository's
`.env`. Leave missing credentials missing and report the gap. Never copy `.env*`,
`AGENTS.md` or configuration from another repository, or print secrets.

## Where behaviour lives

| Area | Files to read first | Related tests |
| --- | --- | --- |
| Entities, DNA and tables | `src/node_dag/types.py`, `dna.py` | `tests/test_dna.py` |
| Node contracts and registration | `nodes/base.py`, `factory.py`, `registry.py` under `src/node_dag/` | `tests/test_dag.py`, `test_registry.py` |
| DAG validation | `src/node_dag/dag.py` | `tests/test_dag.py` |
| Execution, cache and progress | `temporal/dag/activities.py`, `workflow.py`, `src/node_dag/storage.py` | `tests/test_cache.py`, `test_dag.py` |
| Builder, verifier and persistence | `src/node_dag/agent.py`, `temporal/run_hypothesis.py` | `tests/test_agent.py` |
| UI and API | `temporal/ui/app.py`, adjacent HTML, `temporal/run_ui.py` | `tests/test_ui.py`, UI cases in `test_agent.py` |
| Translation initiation prediction | `nodes/tools/ostir_expression/` under `src/node_dag/` | `tests/test_ostir.py` |

The current path is `Hypothesis → build_agent → validated Dag → DagWorkflow →
DagOutput → verify_agent`. The builder's `create_node` registers a **configuration
of existing Python code**. It does not author an implementation. The registry
persists those configurations across hypotheses; this alone is not an iterative
search loop or research memory.

The intended hypothesis loop may add critique/retry and durable tool requests for
human resolution. It is distinct from the outer recoding research loop that
selects reusable algorithms. `HypothesisWorkflow`, `tool_added` signalling and a
critique agent are proposed, not implemented. Do not treat the diagrams' names as
existing APIs, or turn an unavailable-tool request into an executable DAG node.

## Contracts to preserve

- A node currently has one input port and receives the whole list by keyword.
  Its config's `inputs`, `categories` and output declaration must match `run`.
  Schema `x-node` exposes those ClassVars to the builder; keep schemas, tool
  descriptions, registry summaries and runtime behaviour consistent.
- Tools return entities and clear upstream scores. Scorers return one dictionary
  per input, in order, with exactly the declared score names. Filters return one
  boolean per input, aligned with the selected score values, and expose `.yes`
  and `.no`. A new runner must preserve these semantics, empty-input skipping,
  deduplication and `DagOutput` shape.
- `Dag` rejects cycles, unknown sources, port/type mismatches and filters whose
  score column does not reach them. Keep validation at the boundary; do not bypass
  it to accommodate a generated graph. Builder failures use `ModelRetry` with
  actionable errors and bounded retries.
- `Entity.id` hashes kind and sequence. `Table.of` merges identical entities.
  IDs therefore identify sequences, not genes, loci or parent-child lineage. A
  benchmark needs an explicit instance-to-result mapping so recoding, deduplication
  or filtering cannot silently change the denominator or erase failures.
- `Dna` represents in-frame uppercase coding DNA. Keep UTRs, flanks, locus,
  strand and organism metadata separate; do not append arbitrary flanks and then
  translate the entire value as a CDS. New entity kinds require both `Value` and
  `TYPES` to change, with serialization and validation checks.
- Configs are frozen, reject extra fields and compute `config_hash`. Use
  `config.columns()` or the registry reply for column names. Do not invent hashes
  or copy a stale score-column suffix after changing configuration fields.
- Raise a node config's `version` when its implementation changes results. The
  cache hashes configuration, inputs, filter values and version, rather than the
  source code or dependency environment. Put every result-affecting parameter,
  including seeds and biological context, into explicit configuration or input;
  record code and dependency versions for experiments.
- Keep randomness local and explicit. `mutate_synonymous` seeds by configuration
  seed and sequence ID so batch composition does not change a sequence's mutation.
  Changing this behaviour requires a version bump and reproducibility checks.
- Temporal workflow orchestration must remain replay-safe; filesystem writes,
  model calls and computational work belong outside workflow replay. Persist with
  `storage.write_atomic`. `$NODE_DAG_RESULTS` defaults to `results/`, containing
  `nodes/`, `workflows/`, `registry/` and `hypotheses/`. It is local disk, so workers
  on different machines do not automatically share a cache.

## Adding a node or changing a model-facing surface

1. Add `config.py` and `function.py` in the existing `nodes/tools/<name>/` or
   `nodes/filters/<name>/` structure. Scorers currently live under `tools/` too.
2. Declare a unique `name` literal, typed configuration, port, categories and
   output. Add the config to `NodeConfig` and its implementation to `MAPPING` in
   `src/node_dag/factory.py`. Registration is manual today.
3. Test meaningful behaviour, alignment, invalid inputs and determinism as
   applicable. Include the existing contract test in `tests/test_dag.py` and
   relevant cache/registry tests. Regenerate affected example hashes and columns.
4. For prompt, schema or tool changes, use the scripted model tests to exercise
   the full creation/submission/retry path. They prove wiring, not research
   quality. Any claim about improved agent decisions needs an appropriately
   budgeted comparison on fixed inputs with the model and prompt recorded.

If runtime authoring is requested, distinguish registration from code generation.
Validate untrusted code in isolation **before importing it in the trusted process**.
Quarantine failed candidates outside discoverable package paths. A subprocess,
timeout and resource limits contain crashes and resource use; they do not by
themselves prevent filesystem or network access. Establish that boundary before
running authored code with secrets, repository write access or benchmark access.
Automatic discovery must never import quarantine or unvalidated generated code.

## Research rules for recoding work

Apply these when implementing an experiment; the benchmark does not yet exist:

- Define the organism/genetic code, targeted codons, CDS boundaries, immutable
  context, objectives and metric directions before search. Synonymous mutation
  is not a guarantee that all target codons disappear. Check unchanged translated
  protein, unchanged CDS length and zero targeted codons in-frame for every
  candidate. Log hard-constraint violations separately from soft scores.
- Keep a fixed denominator and per-instance outcomes. Empty outputs, missing
  genes, invalid sequences, non-finite scores and execution failures cannot become
  apparent improvements by being omitted from an average. Preserve original and
  recoded sequence linkage outside sequence-derived IDs.
- Split data before optimisation. Development scores may enter prompts; held-out
  sequences, detailed errors, hidden-evaluator parameters and scores must not. Freeze the
  selected algorithm before final evaluation. Predeclare and log every held-out
  access; inspecting it changes what can be claimed about independence.
- Keep benchmark code and hidden evaluation data outside the candidate's writable
  surface. A candidate may modify the design algorithm, not its evaluator. Label
  seeded random stub scores as synthetic and exclude them from scientific results.
- `dna_atom_score` is a plumbing objective with separable codon costs; it is a weak
  discovery benchmark. `ostir_expression` already uses OSTIR and ViennaRNA, but its
  translation-initiation prediction is a proxy. Decide whether the task maximises
  predicted initiation or preserves the original level before choosing a score.
  Neither score establishes cellular fitness, viability or safe genome design.
- Compare against fixed random synonymous, best-of-N and greedy baselines with
  explicit token, candidate-evaluation and wall-clock budgets. The current mutation
  node needs adaptation to serve as a target-codon-elimination baseline. Record
  warm/cold cache conditions and count failed attempts in the budget.
- Pair parent/child outcomes on the same instances and seeds. Report per-gene
  effects, uncertainty, failures and sample size. Adaptive development selection
  needs a separate frozen confirmation; an LLM verdict or multiple-testing
  correction alone cannot establish generalisation. Keep a statistical verdict
  separate from today's `Verdict(achieved, reason)`.
- Retain an experiment manifest and append-only attempt ledger: commit, data
  provenance/split hashes, dependency versions, seed, model/prompt, budgets,
  parent/child DAGs, node versions, per-instance metrics, errors and selection
  decisions. Ablations must use the same evaluator and comparison protocol.

## Collaboration and completion

Coordinate owners when multiple contributors actually work concurrently.
`types.py` and `factory.py` are shared contract points; agree on changes before
editing them in parallel. The five-person breakdown suggests responsibilities,
not permanent access restrictions or an instruction to spawn five agents.

Use `codex/` branches by default, stage only task-owned files and check the final
diff. Create a PR when asked; keep its description tied to the live diff and
validation evidence. Merging remains a separate human decision. Do not commit
credentials, caches, generated runs or local scratch material. For requested
cleanup on macOS, use `/usr/bin/trash` with explicit paths; do not empty Trash.

Finish with what changed, what was verified, remaining limitations and the PR
link when applicable. Update this guide when contracts or commands change; keep
speculative architecture and dated experiment results in the research note.
