# hypothesis

A domain-specific agent that explores hypotheses by composing reproducible workflows
from a collection of tools and decision nodes.

You give it a goal and some inputs. A builder agent writes a hypothesis (how a DAG of
the available nodes can meet the goal) and the DAG itself. The DAG runs as a Temporal
workflow, and a verifier agent judges whether the outcome meets the goal.

- **Reproducible.** A DAG is plain JSON over typed nodes, checked before it runs. The
  same DAG on the same inputs gives the same result.
- **Cached.** Each node's result is cached by its config and inputs, so a step that
  already ran is never computed again, even in a different DAG.
- **Scalable.** Runs are Temporal workflows. Steps that are ready together run in
  parallel, and you add workers to run more.

Every hypothesis, with its DAG, outcome and verdict:

![The hypotheses page](docs/hypotheses.png)

Each run, with the status of every step:

![The runs page](docs/run.png)

## Layout

```
src/node_dag/
  types.py                     shared types (FooBar, Baz), the Value union, TYPES
  nodes/base.py                Category, BaseToolConfig, BaseDecisionConfig, BaseNode
  nodes/tools/<name>/          config.py + function.py
  nodes/decisions/<name>/      config.py + function.py; run returns bool
  factory.py                   NodeConfig union + config -> node class mapping
  dag.py                       Dag / Step; rejects cycles, unknown sources, type mismatches
  agent.py                     Hypothesis; the builder and verifier agents
temporal/
  dag/workflow.py              DagWorkflow: runs each step when its inputs exist
  dag/activities.py            run_tool, run_decision (call the factory), save_workflow
  ui/                          web pages for the runs and the hypotheses
  run_worker.py                the Temporal worker
  run_workflow.py              run a DagInput JSON file
  run_hypothesis.py            build, run and verify a Hypothesis JSON file
  run_ui.py                    serve the web pages
```

## Nodes

A config declares the node's contract as ClassVars:

- `inputs`: port name -> type. `run` takes these as keyword arguments: `run(a=FooBar, b=FooBar)`.
- `output` (tools): the type `run` returns.
- `forwards` (decisions): the input port the decision passes on, on `<step>.yes` or `<step>.no`.
- `categories`: what the node does.

A test checks that `inputs` matches `run`'s signature and that `categories` is set.

To add a node, write `config.py` and `function.py`, then add the config to
`NodeConfig` and `MAPPING` in `factory.py`. To add a shared type, add it to
`Value` and `TYPES` in `types.py`.

## DAGs

```json
{
  "inputs": {"x": "foo_bar", "y": "foo_bar"},
  "steps": {
    "add5":  {"config": {"name": "add", "amount": 5},        "inputs": {"value": "x"}},
    "big":   {"config": {"name": "at_least", "threshold": 10}, "inputs": {"value": "add5"}},
    "total": {"config": {"name": "sum"},                     "inputs": {"a": "big.yes", "b": "y"}},
    "sub1":  {"config": {"name": "add", "amount": -1},       "inputs": {"value": "big.no"}}
  }
}
```

A source is a DAG input, a tool step, or a decision branch. A step runs when all its
sources have a value, and is skipped otherwise. Steps that are ready at the same
time run in parallel.

Results go under `$NODE_DAG_RESULTS` (default `results/`):

- `nodes/<node name>/<hash>.json`: each node's cached result, keyed by its config and inputs.
- `workflows/<workflow id>.json`: each run's DAG, step statuses and values, written when
  the run finishes or fails.
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

http://127.0.0.1:8000/hypotheses lists every saved Hypothesis: its goal, inputs,
hypothesis, outcome and verdict, with its DAG and a link to its run. It reads the files
under `results/`, so it needs no worker.

```bash
temporal server start-dev &
uv run python -m temporal.run_worker --step-delay 2 &
uv run python -m temporal.run_ui &
uv run python -m temporal.run_workflow examples/simple.json
```

## Agents

A `Hypothesis` holds a `goal` and the `inputs` to run it on. `temporal.run_hypothesis`
fills in the rest of the Hypothesis:

1. `hypothesis` and `dag`: the builder agent lists the nodes, reads the schemas it
   needs, and submits a `Dag` with its hypothesis: how that DAG meets the goal. A
   validation error goes back to the model to fix. Each config's schema carries its
   ports, outputs and categories under `x-node`, because a JSON schema leaves
   ClassVars out.
2. `outcome`: the `DagOutput` of running that DAG on Temporal.
3. `verdict`: a second agent answers "Did this workflow complete its goal?". It works
   out the expected result itself and compares it with the outcome.

```bash
temporal server start-dev &
uv run python -m temporal.run_worker &
uv run --with 'pydantic-ai-slim[bedrock]' python -m temporal.run_hypothesis examples/double.json \
  --model bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0
```
