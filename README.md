# hypothesis

A domain-specific agent that explores hypotheses by composing reproducible workflows
from a collection of tools and decision nodes.

You give it a goal, some inputs and what would count as meeting the goal. A builder
agent plans a DAG of the available nodes, the DAG runs as a Temporal workflow, and the
result is judged against the criteria. If it falls short, a critic says why and the
builder tries again. If the plan needs a node nobody has written, the run asks for it
and waits.

- **Reproducible.** A DAG is plain JSON over typed nodes, checked before it runs. The
  same DAG on the same inputs gives the same result.
- **Cached.** Each node's result is cached by its config and inputs, so a step that
  already ran is never computed again, even in a different DAG.
- **Scalable.** Runs are Temporal workflows. Steps that are ready together run in
  parallel, and you add workers to run more.
- **Checked.** Success is computed from criteria frozen before any plan existed, and
  settled by the DAG's own decision steps. No model can award itself a pass.

Every goal, and how many of its hypotheses met it:

![The goals list](docs/goals.png)

Click a goal to list its hypotheses, then a hypothesis to see its criteria, its DAG,
the outcome and the verdict:

![One hypothesis](docs/hypotheses.png)

Each run, with the status of every step:

![The runs page](docs/run.png)

## Layout

```
src/node_dag/
  types.py                     shared types (Dna, AminoAcidSequence, Score), the Value union, TYPES
  dna.py                       genetic code, synonymous codons, atoms per base
  nodes/base.py                Category, BaseToolConfig, BaseDecisionConfig, BaseNode
  nodes/tools/<name>/          config.py + function.py
  nodes/decisions/<name>/      config.py + function.py; run returns bool
  factory.py                   NodeConfig union + config -> node class mapping
  dag.py                       Dag / Step; rejects cycles, unknown sources, type mismatches
  wiring.py                    PortContract, check_wiring: the same check over kind names
  plan.py                      Criterion, Assertion, Plan, Critique, Verdict, Attempt, Hypothesis, accepted()
  agent.py                     the builder, criteria, critique and verifier agents
temporal/
  dag/workflow.py              DagWorkflow: runs each step when its inputs exist
  dag/activities.py            run_tool, run_decision (call the factory), save_workflow
  hypothesis/workflow.py       HypothesisWorkflow: the loop, and the wait for a missing tool
  hypothesis/activities.py     the model calls, and the only read of the node registry
  hypothesis/models.py         what crosses the workflow-activity boundary
  store.py                     where results live on disk
  scaffold_node.py             turn a ToolRequest into a node package; --bump a cache version
  ui/                          web pages for the runs, the hypotheses and the tool requests
  run_worker.py                the Temporal worker
  run_workflow.py              run a DagInput JSON file
  run_hypothesis.py            run a Hypothesis JSON file to a verdict
  run_ui.py                    serve the web pages
```

## Nodes

A config declares the node's contract as ClassVars:

- `inputs`: port name -> type. `run` takes these as keyword arguments: `run(sequence=Dna)`.
- `output` (tools): the type `run` returns.
- `forwards` (decisions): the input port the decision passes on, on `<step>.yes` or `<step>.no`.
- `categories`: what the node does.
- `example`: one worked example, with concrete inputs, the config used and the output.

`example` is required on every config. A `ToolRequest` for a node that does not exist
yet must carry a worked example, so the nodes that do exist have to as well — otherwise
the agent reasons better about hypothetical tools than about the real ones it can
actually use. It reaches the agent through `contract()`, which rides in the config
schema under `x-node` and so is surfaced by `describe_node`.

A test checks that `inputs` matches `run`'s signature and that `categories` is set.

To add a node, write `config.py` and `function.py`, then add the config to
`NodeConfig` and `MAPPING` in `factory.py`. To add a shared type, add it to
`Value` and `TYPES` in `types.py`.

## DAGs

```json
{
  "inputs": {"gene": "dna"},
  "steps": {
    "recoded": {"config": {"name": "recode_codons", "targets": ["TCG", "TCA"]}, "inputs": {"sequence": "gene"}},
    "clean":   {"config": {"name": "codons_absent", "codons": ["TCG", "TCA"]},   "inputs": {"sequence": "recoded"}},
    "protein": {"config": {"name": "dna_to_protein"},                           "inputs": {"sequence": "clean.yes"}},
    "atoms":   {"config": {"name": "dna_atom_score"},                           "inputs": {"sequence": "clean.yes", "reference": "gene"}},
    "bulky":   {"config": {"name": "at_least", "threshold": 400},               "inputs": {"value": "atoms"}}
  }
}
```

A source is a DAG input, a tool step, or a decision branch. A step runs when all its
sources have a value, and is skipped otherwise. Steps that are ready at the same
time run in parallel.

`wiring.py` holds that check over kind names rather than Python types, and `Dag`
delegates to it. The point of the indirection is that a plan naming nodes nobody has
written can be typechecked exactly like a DAG: a requested node declares its ports as
kind names, which is all the check ever needed.

Results go under `$NODE_DAG_RESULTS` (default `results/`):

- `nodes/<node name>/<hash>.json`: each node's cached result, keyed by its config,
  inputs and `version`.
- `workflows/<workflow id>.json`: each run's DAG, step statuses and values, written when
  the run finishes or fails.
- `hypotheses/<hypothesis id>.json`: each Hypothesis, saved at every stage of the loop.
- `requests/<node name>.json`: the contract of one node that was asked for and does not
  exist. The file holds the contract only; who is blocked on it is computed by scanning
  the hypotheses, because two runs blocking on one tool would race a read-modify-write.
- `trajectories/<hypothesis id>-r<n>-<stage>.json`: each agent call's message history.
  Only a path and a token count cross the workflow boundary, so a few rounds of
  transcripts cannot approach Temporal's 2 MB payload limit — but a plan stays
  auditable: which node schemas the builder read, what it tried first, which guard it
  bounced off.
- `briefs/<name>.json`: what the literature says about a goal. Empty until that stage
  exists.

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
inputs. Click a hypothesis to see all of it: inputs, criteria, each round's plan,
outcome and verdict, its DAG step by step, and a link to its run. Its
criteria-vs-assertions panel is the acceptance rule made visible: one row per criterion,
which assertion covers it, and whether that assertion's branch fired. A criterion with
no assertion is spelled out as the reason a run can only end `unverified`. The page
reads the files under `results/`, so it needs no worker.

http://127.0.0.1:8000/requests is the tool backlog: every node that has been asked for
and not written, the one blocking the most runs first, each with its full contract, the
command to scaffold it, and the hypotheses waiting on it.

http://127.0.0.1:8000/new starts a hypothesis: enter a goal, optionally your own
hypothesis for how to meet it, the inputs, the success criteria and how many rounds to
allow. Leave the criteria empty and the criteria agent derives them. The run starts as a
durable workflow and the page jumps to the hypothesis so you can watch it. This needs
`--model` on `run_ui`, and a worker running.

Both pages read `results/`, so they survive a worker restart — which matters, because
registering a new node needs one.

## The loop

A `Hypothesis` holds a `goal`, the `inputs` to run it on, and the `criteria` that decide
whether the goal was met. `HypothesisWorkflow` fills in the rest, one round at a time:

1. **Criteria.** If none were given, the criteria agent derives them from the goal. It
   runs once, before any plan exists, and what it writes is frozen for the whole
   investigation. It is told never to weaken a criterion to make it easy to pass.
2. **Plan.** The builder agent lists the nodes, reads the schemas it needs, and submits
   a `Plan`: a hypothesis in prose, a prediction, the wiring, the assertions that will
   settle it, a reason for every node it chose, and a contract for every node it named
   that does not exist. Eleven pure guards in `submit_plan` reject a bad submission back
   to the model as a retry, so it is a conversation rather than a failure. Each config's
   schema carries its ports, outputs, categories and example under `x-node`, because a
   JSON schema leaves ClassVars out.
3. **Resolve.** The `resolve_plan` activity is the only place the node registry is read.
   It returns the built `Dag` when every node exists, and otherwise the contracts to ask
   a human for. If anything is missing, the run **blocks** and waits.
4. **Run.** The DAG runs as a child `DagWorkflow` on the same task queue.
5. **Verify.** The verifier agent judges the outcome, and the workflow computes
   acceptance from the frozen criteria. Achieved: stop.
6. **Critique.** The critic says what went wrong in terms of this DAG's steps and
   values, picks the single root cause, lists the steps worth keeping, and says what to
   change. That critique goes to the next round's builder, which must say what it
   changed in response.

The builder sees every previous attempt, summarised: full reasoning, but previews of
values rather than the values themselves. It needs to know *that* a step produced a
2970-base sequence, not what its bases were, and three rounds of raw outcomes would
approach the payload limit.

The loop stops when the round was accepted, when `max_rounds` is used up, when the score
has not improved for `patience` rounds, when a plan repeats a wiring fingerprint already
run, or when the token budget runs out. The fingerprint covers the wiring alone, so
re-wording cannot dodge the repeat check. `stopped_because` records which it was.

The loop could not live in the DAG, because `Dag` rejects cycles, which is right. It
could not live in the client process either, because registering a node means restarting
the worker, and a run blocked on a missing tool has to survive that restart and however
many days pass before someone writes the node. So the workflow owns the loop, and the
wait is a Temporal signal holding no timer and no activity.

## Acceptance

A round is achieved only when **every** frozen `Criterion` is covered by an `Assertion`
whose decision branch actually fired, **and** the verifier does not veto it. The verifier
returns an opinion with no `achieved` field at all — just `agrees`, `covers_goal`, a
score and a reason — and `accepted()` computes the rest. A model can veto a result. It
can never certify one.

This is the load-bearing rule, and it is worth being clear about why:

- The critic's whole job is to tell the builder how to satisfy the verifier. If the
  verifier could grant success, the loop would be optimising against its own judge, and
  more rounds would make it worse rather than better. A judge that can only veto cannot
  be talked round.
- The criteria are frozen before any plan exists, so the builder chooses how to sit the
  exam but never what it asks.
- An `Assertion` names a decision step of the plan and the branch that must fire. The
  branch a decision took is recorded by `DagWorkflow`, so the claim is settled by what
  the DAG did and no model judges it.

A plan that asserts nothing ends `unverified`. That is not a failure — it is a request
for the node that could have checked the thing. The verifier is told the same: admitting
that a criterion went unchecked is the most useful thing it can do, and setting
`covers_goal` false on a superficial assertion blocks acceptance.

## Blocked on a tool

The builder names the best node for the goal whether or not anyone has written it. The
guards keep that honest: the cage comes off for unknown node names and stays on for
known ones, so inventing a tool costs *more* precision than using one. A `ToolRequest`
must declare its ports and kinds, use only kinds that already exist, carry a worked
example, name an existing node it considered and say what that node cannot do, and wire
up correctly — or the plan bounces.

When something is missing, the run writes `results/requests/<name>.json`, blocks, and
shows up at `/requests`, ranked by how many runs are stuck behind each name. To unblock
it:

```bash
uv run python -m temporal.scaffold_node <name>
```

That writes `__init__.py`, `config.py` and a `function.py` whose `run` raises
`NotImplementedError` with the contract restated in the message, then prints the two
`factory.py` edits to apply by hand. Write the body of `run`, apply those edits, then
**restart the worker** — `factory.MAPPING` is built at import. Finally click "Tool added
— resume" on the hypothesis, or `POST /api/requests/<name>/tool-added` to resume every
run waiting on that node at once. The held plan is reused, not rewritten: the run picks
up where it blocked.

Each resume re-runs the resolve activity rather than reusing its recorded answer, which
is the only way a replay can see a node that has just been registered. Two things that
would otherwise go wrong are handled instead:

- **Resuming without restarting the worker.** The resolve output carries a hash of this
  worker's node names. Unchanged across a resume means the worker was never restarted,
  so the run re-blocks with a note saying exactly that, rather than silently finding the
  tool still missing.
- **A node written to different port names.** The name resolves and the wiring check
  then fails. Resolve catches that first and re-blocks with a requested-versus-actual
  diff, instead of dying at the moment of success.

## The cache footgun

A node's result is cached on its config, its inputs and `config.version`. So fixing a
bug in `run` without raising `version` serves the stale, buggy result forever. That
bites precisely when you are writing a node mid-run, which is why every generated config
carries `version: ClassVar[int] = 1` and a comment saying to bump it:

```bash
uv run python -m temporal.scaffold_node <name> --bump
```

Then restart the worker, so the new code is picked up.

## Models

The builder and the critic do the reasoning; the verifier is a cheap, independent veto.
**Use a different model for the verifier than for the builder.** The critique loop
exists to push the builder towards satisfying that judge, so a blind spot the two share
would compound every round.

Models use the Anthropic API directly. Put `ANTHROPIC_API_KEY` in a `.env` at the repo
root, which is already gitignored; the worker and the UI both load it from there.

```bash
temporal server start-dev &
uv run python -m temporal.run_worker --step-delay 2 &
uv run python -m temporal.run_ui --model anthropic:claude-fable-5-1 --critique-model anthropic:claude-fable-5-1 --verify-model anthropic:claude-haiku-4-5 &
```

Or run one hypothesis from the command line and print the finished record:

```bash
uv run python -m temporal.run_hypothesis examples/double.json \
  --model anthropic:claude-fable-5-1 \
  --critique-model anthropic:claude-fable-5-1 \
  --verify-model anthropic:claude-haiku-4-5 \
  --max-rounds 3
```

`examples/double.json` and `examples/score.json` are Hypothesis files: a goal, its
criteria and its inputs. `examples/simple.json` is a DagInput, for running one DAG
directly:

```bash
uv run python -m temporal.run_workflow examples/simple.json
```

## Nodes/tools

We have $150 in Modal credits. You can use these inside a node to run on larger machines or on GPUs.

We also have $20 of HuggingFace Jobs, which is pretty similar.
