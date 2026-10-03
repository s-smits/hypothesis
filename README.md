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
  agent.py                     Hypothesis; the builder and verifier agents
temporal/
  dag/workflow.py              DagWorkflow: runs each step on its whole table once it exists
  dag/activities.py            run_tool, run_score, run_filter (call the factory), save_workflow
  ui/                          web pages for the runs and the hypotheses
  run_worker.py                the Temporal worker
  run_workflow.py              run a DagInput JSON file
  run_hypothesis.py            build, run and verify a Hypothesis JSON file
  run_ui.py                    serve the web pages
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
- `hypotheses/<hypothesis id>.json`: each Hypothesis, saved after each stage of
  `run_hypothesis`. Its run's workflow ID is the hypothesis ID.

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
inputs. Click a hypothesis to see all of it: inputs, hypothesis, outcome, verdict, its
DAG step by step, and a link to its run. It reads the files
under `results/`, so it needs no worker.

http://127.0.0.1:8000/new starts a hypothesis: enter a goal, optionally your own
hypothesis for how to meet it, and the inputs. The server then runs the builder agent,
the DAG and the verifier in the background, and the page jumps to the hypothesis so you
can watch it. This needs `--model` (and optionally `--verify-model`) on `run_ui`, and a
worker running.

http://127.0.0.1:8000/nodes lists every node in the registry, as the builder agent sees
it: its description, input port, outputs, the full name of each score column a scorer
adds, the column a filter reads, and its config. It has a search box, and reads the
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

## Agents

A `Hypothesis` holds a `goal` and the `inputs` to run it on: a list of entities for each
input name, as in `examples/optimise.json`. `temporal.run_hypothesis`
fills in the rest of the Hypothesis:

1. `hypothesis` and `dag`: the builder agent makes the nodes it needs one at a time, then
   wires them. It lists the kinds of node (`list_nodes`) and the nodes already in the
   registry (`list_registry`), reads the schemas it needs (`describe_node`), and calls
   `create_node` with a config and a short description for each node. A node's reply
   shows its id (`<node name>__<config hash>`), its input port and kind, its outputs
   and, for a scorer, the full name of each score column it adds
   (`<node name>__<config hash>__<score name>`). A filter can only be made on a column
   that a registered scorer adds, so the builder makes the scorer first. Then it submits
   a `Dag` with its hypothesis: how that DAG meets the goal. Each step names a registered
   node by id, so only nodes made with `create_node` can be used. A validation error
   goes back to the model to fix. Each config's schema carries its ports, outputs and
   categories under `x-node`, because a JSON schema leaves ClassVars out.

   The registry is `$NODE_DAG_RESULTS/registry/<node id>.json`, one file per node. It
   lasts across hypotheses, so a builder can reuse a node that an earlier one made.
2. `outcome`: the `DagOutput` of running that DAG on Temporal.
3. `verdict`: a second agent answers "Did this workflow complete its goal?". It works
   out the expected result itself and compares it with the outcome.

```bash
# Start the server
temporal server start-dev
# Start the worker
uv run python -m temporal.run_worker
# Start the UI
uv run python -m temporal.run_ui --model anthropic:claude-haiku-4-5
```

## Nodes/tools

We have $150 in Modal credits. You can use these inside a node to run on larger machines or on GPUs.

We also have $20 of HuggingFace Jobs, which is pretty similar.
