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
  types.py                     shared types (FooBar, Baz, Dna, Score), the Value union, TYPES
  dna.py                       genetic code, synonymous codons, atoms per base
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

Models use the Anthropic API directly, so set `ANTHROPIC_API_KEY` first.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
temporal server start-dev &
uv run python -m temporal.run_worker --step-delay 2 &
uv run python -m temporal.run_ui --model anthropic:claude-haiku-4-5 &
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
# Start the server
temporal server start-dev
# Start the worker
uv run python -m temporal.run_worker
# Start the UI
uv run python -m temporal.run_ui --model anthropic:claude-haiku-4-5


## Nodes/tools

We have $150 in Modal credits. You can use these inside a node to run on larger machines or on GPUs.

We also have $20 of HuggingFace Jobs, which is pretty similar.