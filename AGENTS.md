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
are designs under discussion, not claims that their proposed components exist. Their
tables of what exists were checked against `f9d2f50`, before the loop, the run ledger
and the benchmark harness; this guide and the README describe the current code.
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
need `ANTHROPIC_API_KEY`. `run_hypothesis` and `run_ui` take `--model` (default
`anthropic:claude-sonnet-5-5`) for the builder and also for the inputs, criteria and critique
calls, and `--verify-model` (default `anthropic:claude-haiku-4-5`) for the verifier alone; both are
optional. Nothing checks that they differ, though the help text asks for it. A proposed model
string must be checked against the chosen provider. `temporal/run_hypothesis.py` loads the repository's
`.env`. Leave missing credentials missing and report the gap. Never copy `.env*`,
`AGENTS.md` or configuration from another repository, or print secrets.

## Where behaviour lives

| Area | Files to read first | Related tests |
| --- | --- | --- |
| Entities, DNA and tables | `src/node_dag/types.py`, `dna.py` | `tests/test_dna.py` |
| Node contracts and registration | `nodes/base.py`, `factory.py`, `registry.py` under `src/node_dag/` | `tests/test_dag.py`, `test_registry.py` |
| DAG validation | `src/node_dag/dag.py` | `tests/test_dag.py`, `test_beats_reference.py` |
| Execution, cache and progress | `temporal/dag/activities.py`, `workflow.py`, `src/node_dag/storage.py` | `tests/test_cache.py`, `test_dag.py` |
| Builder, verifier and persistence | `src/node_dag/agent.py`, `temporal/hypothesis/activities.py`, `temporal/run_hypothesis.py` | `tests/test_agent.py` |
| Hypothesis loop, plan checks, node scaffolding | `temporal/hypothesis/`, `src/node_dag/plan.py`, `check_plan` in `src/node_dag/agent.py`, `temporal/scaffold_node.py` | `tests/test_loop.py`, `test_plan.py`, `test_guards.py`, `test_scaffold.py` |
| Watching runs | `temporal/pulse.py` | `tests/test_pulse.py` |
| Run ledger | `temporal/ledger.py` | `tests/test_ledger.py`, `test_loop.py` |
| Benchmark harness and goal export | `src/node_dag/benchmark.py`, `temporal/run_benchmark.py` | `tests/test_benchmark.py`, `test_benchmark_goals.py` |
| UI and API | `temporal/ui/app.py`, adjacent HTML, `temporal/run_ui.py` | `tests/test_ui.py`, UI cases in `test_agent.py` |
| Translation initiation prediction | `nodes/tools/ostir_expression/` under `src/node_dag/` | `tests/test_ostir.py` |
| Structure folding and comparison | `nodes/tools/esmfold2_fold/`, `tmalign/` under `src/node_dag/` | `tests/test_esmfold2.py`, `test_tmalign.py` |
| Literature search and observations | `src/node_dag/amass.py` | `tests/test_amass.py` |
| Benchmark gene set | `src/node_dag/genes.py`, `data/ecoli_k12_cds.json` | `tests/test_genes.py` |

One round is `Hypothesis → build_agent → validated Dag → DagWorkflow → DagOutput →
verify_agent`. The builder's `create_node` registers a **configuration of existing
Python code**. It does not author an implementation. The registry persists those
configurations across hypotheses; this alone is not an iterative search loop or
research memory.

The `/new` page takes a goal and its success criteria, each a row with a kind
(quantitative or qualitative) and a claim: the user writes them, or `criteria_agent`
drafts them through `POST /api/criteria` and the user edits the draft. It does not ask
for inputs: the loop takes them from the goal before round 1, and `POST /api/hypotheses`
still takes `inputs` from a caller who has them. A file dropped on the goal goes through
`POST /api/files` to `results/files/<id>` — the id a hash of its contents — and a
`FASTAFile <id>` mention is put in the goal; the inputs stage resolves the mention to
the file itself, so no model ever copies its contents. Nothing fetches an input: `add_input`
accepts any entity kind in `TYPES` as an object, and an entity the model wrote out rather
than was given is unverified. The hypotheses page marks each
criterion met, not met or unclear from the assertions the plan set against it
(`Attempt.held`), never from a model's call.
`Hypothesis.observations` holds the literature the run is built on. The `/new` page
gathers it before the build: `observations_agent` searches Amass through
`POST /api/observations` and the user edits or drops each summary. The planner sees the
kept list in its prompt and may cite it in the plan's observations; `cite` in
`src/node_dag/agent.py` merges the citations into the list, so a record that goes
uncited stays on the hypothesis. An agent can only cite a record Amass actually returned
to it or one the user kept, so a citation is not invented; it is still only the model's
reading of that record, and it does not establish that the record justifies the
configuration field or threshold beside it.

`HypothesisLoop` (`temporal/hypothesis/`) repeats rounds: it fixes the criteria and
critiques a missed round. With `allow_requests` it also blocks on a requested node
until `tool_added` is signalled. That flag is off by default: `check_plan` sends back
a plan carrying `requests`, so the builder composes from the nodes that exist, and a
node that goes missing another way ends the run rather than waiting. Turn it on with
`--allow-requests` on `run_hypothesis`, or `allow_requests` in `POST /api/hypotheses`,
when you intend to write the node. It is distinct from the outer recoding research
loop that selects reusable algorithms. `docs/intended-structure.md` is a design sketch of `main`
before the loop existed; do not treat its names as APIs, or turn an
unavailable-tool request into an executable DAG node.

## Watching a run

While a hypothesis run is open, make the last action of each reply a look:

```sh
uv run python -m temporal.pulse
```

It reads the saved hypotheses and trajectories under `$NODE_DAG_RESULTS`, and the node sources
and `factory.py` to see which requested nodes now exist. It never
calls a model, a worker or the API, so it is safe to run at any time.
It keeps what it saw in `<results>/pulse.json`, so each look says only what moved since the
last one. The first look has nothing to differ from and prints status lines only. Do not
loop it inside a reply; the next reply's look is the next reading. `--every 30` keeps
looking for a person at a terminal, `--json` is for another program, and an id, label or part
of the goal selects one run.

Each line starts with a mark:

- `◆` something happened: criteria fixed, a round opened, a plan accepted, how many
  assertions held (`r1 assertions 2/4; did not hold: improved.produced`: the count is per
  assertion, the names are each failed `step.branch`), blocked, a verdict, the run ended.
  Report it; no action needed.
- `⚠` something to act on or decide (below).
- `·` a detail, such as a model call that went through, or an alert that cleared.

An event ends with `→ path` under the results directory; read that file before guessing.
An alert is said once when it starts and once when it clears, so silence on a later look
does not mean it is fixed. The status line keeps showing the state.

| Alert | What it means | What to do |
| --- | --- | --- |
| a call `sent back for: …` | A model call was rejected three or more times. | Read the reasons. The same one repeating means a guard message or schema is unclear; fix that, not the run. |
| `blocked 30m00s on …` | A person has to write a node. | Follow the `↳ waiting on` line (below). |
| `400k of 500k tokens used` | The budget is 80% spent; reaching it ends the run. | Let it end, or abandon. |

A blocked run lists each requested node as `missing`, `scaffolded`, `unregistered` or
`ready`, with the next step for it: `python -m temporal.scaffold_node <name>`, then write
`run()`, then edit `factory.py`. When every node is `ready`, restart the worker if it started
before the nodes existed, then Resume (or POST `/api/hypotheses/<id>/tool_added`). The same
plan resolves again without another model call.

The header names any file it could not read as a Hypothesis, with the reason. Usually the
file predates a change to a node's config and its `config_hash` no longer matches. That is
not a fault in a run; leave it, and do not edit the hash to make it load. If the look finds no
run, the header says which results directory it searched; check `NODE_DAG_RESULTS`.

## The run ledger

Every run that ends, achieved or not, abandoned or failed, adds one line to
`$NODE_DAG_RESULTS/ledger.jsonl`. `HypothesisLoop` calls the `record_ledger` activity by name
as its last step, and `temporal/ledger.py` builds the line from pulse's reading of the run, so
the ledger and `pulse` always agree on what a run did. Nothing in it is judged by a model: each
value is read from the saved Hypothesis and the saved model calls. A line has the run, its goal,
how it ended and why, the rounds and the assertions that held in each (`3/4`, counted per
assertion, so two criteria asserted on one step and branch count twice; a line written before
that counted each step and branch once), the tokens and seconds spent, the guard
retries, errors and repeated wirings, the nodes used and the ones it asked for, the model per
stage, and a one-line summary. A ledger that cannot be written never changes how a run ended. A
worker that predates the activity must be restarted before runs it starts will be recorded.

To read it: `jq -r '[.ended[:16], .hypothesis, .state, .rounds, .tokens, .summary] | @tsv' results/ledger.jsonl`.

## Contracts to preserve

- A node receives the whole list on each input port, by keyword. A scorer or filter
  has exactly one port, which cannot be optional; only a tool may have several
  (`tmalign` takes a structure and a reference), and only a tool may name some in
  `optional_inputs` (`esmfold2_fold`'s `partner`). A plan may leave an optional port
  unwired, and `run` is still given it as an empty list, so every declared port is a
  parameter of `run` either way. At least one port must stay required: the workflow
  skips a step when a port it must wire has nothing to run on, and an empty optional
  port is simply not used. Its config's `inputs`, `categories` and output declaration
  must match `run`. Schema `x-node` exposes those ClassVars to the builder, including
  which ports are optional; keep schemas, tool descriptions, registry summaries and
  runtime behaviour consistent.
- Tools return entities and clear upstream scores. Scorers return one dictionary
  per input, in order, with exactly the declared score names. Filters return one
  boolean per input, aligned with the selected score values, and expose `.yes`
  and `.no`. A filter whose config returns `reads_reference()` (`beats_reference`)
  is also given that entity's score as `reference`. The workflow reads it from the
  table of the step named in `scored_in`, and `Step.deps()` makes that step run
  first. A new runner must preserve these semantics, empty-input skipping,
  deduplication and `DagOutput` shape.
- `Dag` rejects cycles, unknown sources, port/type mismatches and filters whose
  score column does not reach them, and reference filters whose `scored_in` step
  lacks that column. Keep validation at the boundary; do not bypass it to
  accommodate a generated graph. Builder failures use `ModelRetry` with
  actionable errors and bounded retries.
- A round is accepted by `accepted()` in `plan.py`, never by a model. Every criterion
  needs an assertion, every assertion must hold on the outcome, and the verifier, which may
  only veto, must set both `agrees` and `covers_goal`. `yes` or `no` on a filter holds when
  that branch took at least one entity and the other none. `produced` holds when the step,
  or a filter's `yes` branch, gave at least one entity, and on a filter it never covers
  what the kept entities hold. Keep `holds`, the builder's guards (`check_plan`) and the
  prompts agreeing on these meanings.
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
  cache hashes configuration, inputs, filter values, a
  filter's reference score and version, rather than the source code or dependency
  environment. Put every result-affecting parameter,
  including seeds and biological context, into explicit configuration or input;
  record code and dependency versions for experiments.
- Keep randomness local and explicit. `mutate_synonymous` seeds by configuration
  seed and sequence ID so batch composition does not change a sequence's mutation.
  Changing this behaviour requires a version bump and reproducibility checks.
- Temporal workflow orchestration must remain replay-safe; filesystem writes,
  model calls and computational work belong outside workflow replay. Persist with
  `storage.write_atomic`. `$NODE_DAG_RESULTS` defaults to `results/`, containing
  `nodes/`, `workflows/`, `registry/`, `hypotheses/`, `requests/` and `trajectories/`,
  plus `ledger.jsonl`, `pulse.json`, `ledger/` (the benchmark's attempts, one file
  each), `links/` (what nodes report), `files/` (uploads a goal names as
  `FASTAFile <id>`) and the `amass/` reply cache. It is
  local disk, so workers on different machines do not automatically share a cache.
- A file that cannot be written must not change how a run ended. `save_workflow` gets
  three attempts, then the workflow logs the failure and the run still ends as it would
  have; the ledger line and the model-call transcripts are treated the same way.
  `POST /api/hypotheses` answers 422 for inputs that cannot run, before anything is saved,
  and 503 when the workflow cannot start, after saving the Hypothesis as `failed` so none
  is left `building`.

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

Apply these when implementing an experiment. A first deterministic harness exists
(`src/node_dag/benchmark.py`, run by `temporal/run_benchmark.py`): genes picked
deterministically from a committed gene set (`src/node_dag/data/ecoli_k12_cds.json`: 400
real E. coli K-12 CDS from NC_000913.3, with their selection and a digest in the file's
`provenance`), random, best-of-N, greedy and exact baselines, a development/held-out split
and an append-only attempt ledger. It uses no model and runs no DAG, so a search built on
it still has to meet these:

- Define the organism/genetic code, targeted codons, CDS boundaries, immutable
  context, objectives and metric directions before search. Synonymous mutation
  is not a guarantee that all target codons disappear. Check unchanged translated
  protein, unchanged CDS length and zero targeted codons in-frame for every
  candidate. Log hard-constraint violations separately from soft scores.
- Benchmark goals exported with `--emit-goals` include `Instance.fixed()`. Use
  `constraint_check` version 2 with those indices in `immutable`, and filter its
  `immutable_unchanged` column at 1.0. Empty `immutable` checks no positions and
  reports 1.0 anyway, so read `immutable_checked` with it.
  Held-out exports require the same recorded release as scoring.
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
- `ostir_expression` already uses OSTIR and ViennaRNA, but its translation-initiation
  prediction is a proxy. Decide whether the task maximises predicted initiation or
  preserves the original level before choosing a score. A plumbing objective with
  separable codon costs, like the removed `dna_atom_score`, is a weak discovery
  benchmark. No score establishes cellular fitness, viability or safe genome design.
- Compare against fixed random synonymous, best-of-N and greedy baselines with
  explicit token, candidate-evaluation and wall-clock budgets. The current mutation
  node needs adaptation to serve as a target-codon-elimination baseline. Record
  warm/cold cache conditions and count failed attempts in the budget.
- Pair parent/child outcomes on the same instances and seeds. Report per-gene
  effects, uncertainty, failures and sample size. Adaptive development selection
  needs a separate frozen confirmation; an LLM verdict or multiple-testing
  correction alone cannot establish generalisation. Keep a statistical verdict
  separate from today's `Verdict`, whose `achieved` is set by `accepted()`.
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
