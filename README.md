# hypothesis

A domain-specific agent that explores hypotheses by composing reproducible workflows
from a collection of tools, scorers and filters.

You give it a goal and some inputs. A builder agent writes a hypothesis (how a DAG of
the available nodes can meet the goal) and the DAG itself. The DAG runs as a Temporal
workflow, and a verifier agent judges whether the outcome meets the goal.

Contributor guidance lives in [AGENTS.md](AGENTS.md). The proposed recoding
experiment, possible research directions and implementation priorities are in
[docs/research-directions.md](docs/research-directions.md). The tentative agent
and workflow architecture is in [docs/intended-structure.md](docs/intended-structure.md).

- **Reproducible.** A DAG is plain JSON over typed nodes, checked before it runs. The
  same DAG on the same inputs gives the same result.
- **Cached.** Each node's result is cached by its config and inputs, so a step that
  already ran is never computed again, even in a different DAG.
- **Scalable.** Runs are Temporal workflows. Steps that are ready together run in
  parallel, and you add workers to run more.

Every goal, and how many of its hypotheses met it:

![The goals list](docs/goals.png)

Click a goal to list its hypotheses, then a hypothesis to see its DAG, outcome and
verdict:

![One hypothesis](docs/hypotheses.png)

Each run, with the status of every step:

![The runs page](docs/run.png)

## Layout

```
src/node_dag/
  types.py                     entities (Dna, Rna, AminoAcidSequence, ProteinStructure) with an id, Score, Table, TYPES
  dna.py                       genetic code, synonymous codons, atoms per base
  nodes/base.py                Category, BaseToolConfig, BaseScoreConfig, BaseFilterConfig, BaseNode
  nodes/tools/<name>/          config.py + function.py; tools make entities, scorers score them
  nodes/filters/<name>/        config.py + function.py; run returns a bool for each entity
  factory.py                   NodeConfig union + config -> node class mapping
  dag.py                       Dag / Step; rejects cycles, unknown sources, type and score column mismatches
  plan.py                      Plan, ToolRequest, Assertion, accepted(): the acceptance rule
  agent.py                     Hypothesis; the builder, verifier, critique and criteria agents
temporal/
  dag/workflow.py              DagWorkflow: runs each step on its whole table once it exists
  dag/activities.py            run_tool, run_score, run_filter (call the factory), save_workflow
  hypothesis/                  HypothesisLoop and its model-call activities
  ui/                          web pages for the runs and the hypotheses
  run_worker.py                the Temporal worker
  run_workflow.py              run a DagInput JSON file
  run_hypothesis.py            run a Hypothesis JSON file through the loop
  run_ui.py                    serve the web pages
  scaffold_node.py             write a requested node's package, all but run()
  pulse.py                     what changed in the open runs since the last look
  ledger.py                    one line per finished run, read the way pulse reads it
```

## Nodes

A DAG works like Pipeline Pilot or KNIME. You pass in a list of entities for each
input, and each node runs once on the whole list that reaches it. An entity (`Dna`, `Rna`,
`AminoAcidSequence`, `ProteinStructure`) has an `id`: a hash of its kind and sequence, so the same
sequence always has the same id and identical entities merge into one.

What flows along an edge is a `Table`: the entities, and their scores so far as
columns keyed by entity id. A column is named `<node name>__<config hash>__<score name>`.

A config declares the node's contract as ClassVars:

- `inputs`: the one port name -> entity type. `run` takes the list of entities by that
  name: `run(sequence=[Dna, ...])`.
- `categories`: what the node does.

There are three kinds of node:

- **Tool** (`BaseToolConfig`): `output` is the entity type. `run` returns the new
  entities, which replace the old and have no scores.
- **Scorer** (`BaseScoreConfig`): `output` maps each score name to `Score`, like
  `{"atom_count": Score}`. `run` returns one `{score name: Score}` dict per entity. The
  entities pass through, with a column added for each score.
- **Filter** (`BaseFilterConfig`): has a `column`. `run(items, values)` gets the
  entities and that column's values and returns a bool for each. The entities that get
  True go to `<step>.yes`, the rest to `<step>.no`, both with their scores.

Every config has a `config_hash`: a hash of its name, version and fields, set when the
config is made. A config with a different hash is rejected, so leave it out. Two scorers
with different fields (say, a different reference) make different columns. Because the
DAG is checked before it runs, a filter on a column that nothing upstream makes is an
error that lists the columns it could have used.

A test checks that `inputs` matches `run`'s signature and that `categories` is set.

To add a node, write `config.py` and `function.py`, then add the config to
`NodeConfig` and `MAPPING` in `factory.py`. To add an entity type, add it to
`Value` and `TYPES` in `types.py`.

## DAGs

```json
{
  "inputs": {"seqs": "dna"},
  "steps": {
    "mutated": {"config": {"name": "mutate_synonymous", "seed": 1, "count": 2}, "inputs": {"sequence": "seqs"}},
    "scored":  {"config": {"name": "dna_atom_score", "reference": {"kind": "dna", "sequence": "ATGGCTCTGAAATAA"}},
                "inputs": {"sequence": "mutated"}},
    "smaller": {"config": {"name": "at_most", "column": "dna_atom_score__3745d4af__atom_count", "threshold": 494},
                "inputs": {"items": "scored"}}
  }
}
```

A source is a DAG input, a tool or scoring step, or a filter branch. A step runs once
its source has a table, and is skipped if the table is empty. Steps that are ready at
the same time run in parallel. `examples/simple.json` is a full run.

Results go under `$NODE_DAG_RESULTS` (default `results/`):

- `nodes/<node name>/<hash>.json`: each node's cached result, keyed by its config and inputs.
- `workflows/<workflow id>.json`: each run's DAG, step statuses and tables (entities and
  score columns), written when the run finishes or fails.
- `registry/<node id>.json`: each node a builder agent made, with its description.
- `hypotheses/<hypothesis id>.json`: each Hypothesis, saved after each stage of the loop.
  The workflow ID is the hypothesis ID.
- `requests/<node name>.json`: each node a plan asked for that does not exist yet.

```bash
uv sync
uv run pytest
temporal server start-dev &
uv run python -m temporal.run_worker
```

## UI

`temporal.run_ui` serves a page at http://127.0.0.1:8000 that lists the DagWorkflow
runs and draws the selected run's DAG with [nice-dag](https://github.com/eBay/nice-dag).
Each step shows its status: pending, running, done, skipped or failed. The page reads
the workflow's `progress` query every 2 seconds, so a worker must be running to answer
it. `--step-delay` makes each step sleep first, so you can watch a run progress.

A run has two views, switched at the top and kept in the URL (`?run=…&view=table`).
**Table** is every sequence the run touched as rows (its `id`, kind and display string,
which for DNA is the codons spaced out) by one column per score, named by score, node and
config hash. **Graph** is the DAG. Click a node and the panel below it shows either
**Sequences × scores** or **Config & inputs**, chosen by a toggle. For sequences: an
input or a tool shows the sequences in and out, a scorer shows its inputs with the score
columns it added, and a filter shows every input row as kept or filtered out, with the
column it filters on highlighted. Config & inputs shows the node's name, `config_hash` and
fields, what feeds each port, and what the step produced. Deselect with the button, Esc or
a click on nothing.

http://127.0.0.1:8000/hypotheses lists every goal with a count of its hypotheses by
status. Click a goal to list its hypotheses, each with its status, a summary and its
inputs. Click a hypothesis to see all of it: inputs, criteria and whether each held, every
round, outcome, verdict, its DAG step by step, and a link to its run. A blocked one has
Resume and Abandon buttons. It reads the files
under `results/`, so it needs no worker.

http://127.0.0.1:8000/new starts a hypothesis: enter a goal, optionally your own
hypothesis for how to meet it, criteria (one per line) and the inputs. The server then
starts the loop in the background, and the page jumps to the hypothesis so you can watch
its rounds. This needs `--model` (and optionally `--verify-model`) on `run_ui`, and a
worker running.

http://127.0.0.1:8000/nodes lists every node in the registry, as the builder agent sees
it: its description, input port, outputs, the full name of each score column a scorer
adds, the column a filter reads, and its config. Below them, the nodes that plans have requested
and the runs waiting on each. It has a search box, and reads the
files under `results/registry/`, so it needs no worker and updates as agents register
nodes.

Models use the Anthropic API directly, so set `ANTHROPIC_API_KEY` first.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
temporal server start-dev &
uv run python -m temporal.run_worker --step-delay 2 &
uv run python -m temporal.run_ui --model anthropic:claude-haiku-4-5 &
uv run python -m temporal.run_workflow examples/simple.json
```

### Evaluating the UI with Claude

`.mcp.json` adds the [Playwright MCP server](https://github.com/microsoft/playwright-mcp),
so Claude Code can drive the UI in a browser. With the UI running, open Claude Code in
this repo, approve the `playwright` server, and ask it something like "Open
http://127.0.0.1:8000/hypotheses, click through a goal and a hypothesis, and report
anything broken or confusing". It reads the page's accessibility tree, clicks, fills in
forms, reads console errors and takes screenshots. Screenshots go to
`results/playwright/`. Each session starts with a fresh browser profile (`--isolated`).
It needs Node (`npx`) and Chrome.

## The loop

A `Hypothesis` holds a `goal`, the `inputs` to run it on (a list of entities per input
name, as in `examples/optimise.json`) and optional `criteria`: claims the result must
satisfy. `HypothesisLoop` (`temporal/hypothesis/`) runs it as a durable Temporal
workflow. Only the model calls are non-deterministic, and each is an activity:

1. **Criteria.** If you gave none, an agent derives them from the goal. They are frozen.
2. **Plan.** The builder agent makes the nodes it needs (`create_node`), reuses
   registered ones, and submits a `Plan`: the wiring, plus one assertion per criterion
   saying which filter branch must hold every entity and which none. Guards reject a
   bad plan (wrong inputs, an uncovered criterion, a repeat of an earlier plan) before
   anything runs, and the agent fixes it.
3. **Run.** The plan becomes a `Dag` and runs as the `DagWorkflow` child.
4. **Verify.** A second agent reads the outcome and can only veto.
5. **Critique.** If the round failed, a third agent says why. The next plan must address it.

Each model call writes its full message history, failed calls included, to
`results/trajectories/<hypothesis id>-r<round>-<stage>.json` (`criteria` is round 0). That is
where to look for which nodes the builder read and which guard it bounced off.

Rounds stop at `max_rounds` (default 3; `--max-rounds` on `run_hypothesis`) or 500,000 tokens. The stop reason is
`stopped_because`, and every round is kept in `attempts`.

A model call whose request grows past 150,000 input tokens (the largest seen is 27,000) has
Anthropic summarise its context server-side, so the call carries on from the summary. This
applies to every stage of the loop whose model can compact (Sonnet 4.6 and 5, Opus 4.6 to 5,
and so on, not `claude-haiku-4-5`, which the API refuses it on) and to nothing outside the loop.
The summary is kept in the call's transcript as a `compaction` part, `pulse` flags the call, and
the tokens spent writing it count towards the budget. Below the window it changes nothing but
adds about 42 input tokens to each request, which the API puts there once compaction is enabled.
Set `NODE_DAG_COMPACT_WINDOW` for the worker to change the window (at least 50,000) or to `0`
to turn it off.

**Acceptance is code, not a model.** `accepted()` in `plan.py` passes a round only if
every criterion has an assertion, every assertion holds on the outcome, and the verifier
agrees. A model cannot grant that, only veto it.

**Blocked on a tool.** A plan may request at most three nodes that do not exist yet, with
a contract (purpose, ports, example) and why none can be composed from the registry. It
type-checks against stand-ins, saves the request under `results/requests/`, and the run
shows `blocked`. `uv run python -m temporal.scaffold_node <name>` writes the node's
`config.py` and `function.py` from the request, with the same ports, fields and config hash
the plan was checked against, and prints the `factory.py` edits. Write `run()`, make the
edits, restart the worker, and click Resume on the nodes page (or POST
`/api/hypotheses/<id>/tool_added`); the same plan is resolved again without a new model
call. `abandon` ends it. `examples/recode_acg_mock.json` is a synthetic gene with two ORFs in
different frames, which no registered node can recode in both, so it blocks in round 1.

**Watching runs.** `uv run python -m temporal.pulse` prints a status line per open run, and on
every later look what moved since the last: criteria fixed, a round opened, a plan accepted,
blocked, the verdict. It warns about a model call sent back three or more times (and says what
for), a plan that wires the same DAG as an earlier round, rounds that come no closer, a working
run with no worker, and a budget nearly spent. For a blocked run it says what each requested node
still needs. It only reads files and the process table; `--every 30` keeps looking, and `--json`
prints the look for another program; `--no-host` skips the process table for a worker started inside another process. Readings are kept in `results/pulse.json`. `AGENTS.md` says what each alert means and what to do.

**Ledger.** Every run that ends adds one line to `results/ledger.jsonl`: its goal, how and why it
ended, the rounds and what held in each, tokens and seconds, guard retries, errors and repeated
wirings, the nodes it used and asked for, and the model per stage. The values are read from the
saved files the way `pulse` reads them, with no model involved. List it with
`jq -r '[.ended[:16], .hypothesis, .state, .rounds, .tokens, .summary] | @tsv' results/ledger.jsonl`.

**Cache warning.** Node results are cached by config and inputs. If you change what a
node does, bump its `version` (`scaffold_node <name> --bump`), or old results are served.

```bash
# Start the server
temporal server start-dev
# Start the worker
uv run python -m temporal.run_worker
# Start the UI
uv run python -m temporal.run_ui --model anthropic:claude-sonnet-5-5 --verify-model anthropic:claude-haiku-4-5
```

## Nodes/tools

We have $150 in Modal credits. You can use these inside a node to run on larger machines or on GPUs.

We also have $20 of HuggingFace Jobs, which is pretty similar.
