import json
import uuid
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.models import Model

from node_dag.dag import Dag, DagOutput
from node_dag.factory import MAPPING
from node_dag.types import TYPES, Value

NODES = {c.model_fields["name"].default: c for c in MAPPING}


class Verdict(BaseModel):
    """The verifier's answer to "Did this workflow complete its goal?".

    Args:
        achieved: True only when the outcome does what the goal asks, for every input.
        reason: The expected result, the actual result, and how they compare.
    """

    achieved: bool
    reason: str


class Hypothesis(BaseModel):
    """A goal, a plan to meet it with a DAG, and what happened when the plan ran.

    Set ``goal`` and ``inputs``. ``run_hypothesis`` fills in the rest, in order:
    ``hypothesis`` and ``dag`` (from the builder agent), ``workflow_id`` and
    ``outcome`` (the DagWorkflow run) and ``verdict`` (from the verifier agent).

    Args:
        id: Names the saved file. Generated if not given.
        goal: What the DAG must do, in plain English, e.g. "double the input integers".
        inputs: The values to run on, keyed by DAG input name.
        hypothesis: How the builder agent will build and run a DAG to meet ``goal``.
        dag: The DAG the builder agent made.
        workflow_id: The ID of the DagWorkflow run that ran ``dag``.
        outcome: The result of running ``dag`` on ``inputs``.
        verdict: Whether ``outcome`` meets ``goal``.
    """

    id: str = Field(default_factory=lambda: f"hypothesis-{uuid.uuid4()}")
    goal: str
    inputs: dict[str, Value]
    hypothesis: str | None = None
    dag: Dag | None = None
    workflow_id: str | None = None
    outcome: DagOutput | None = None
    verdict: Verdict | None = None

    def input_kinds(self) -> dict[str, str]:
        """The DAG inputs the builder must declare: name to kind."""
        return {k: v.kind for k, v in self.inputs.items()}


class DraftStep(BaseModel):
    """One step of a DAG draft.

    Args:
        config: The node config, ``{"name": <node name>, <field>: <value>, ...}``.
            Read the fields with describe_node.
        inputs: Maps each input port of the node to a source: a DAG input name, a
            tool step key, or ``<decision step>.yes`` / ``<decision step>.no``.
    """

    config: dict[str, Any]
    inputs: dict[str, str]


def list_nodes() -> str:
    """List every node: name, categories, input ports, outputs and a summary."""
    # ponytail: lists every node. Add a category filter when the list is too long.
    return "\n".join(
        f"{name} {json.dumps(c.contract())}: {(c.__doc__ or '').splitlines()[0]}"
        for name, c in NODES.items()
    )


def describe_node(name: str) -> dict[str, Any]:
    """Return the config schema of one node: its fields, docs, ports and outputs.

    Args:
        name: A node name from list_nodes.
    """
    if name not in NODES:
        raise ModelRetry(f"Unknown node {name!r}. Known nodes: {sorted(NODES)}")
    return NODES[name].model_json_schema()


def submit_dag(
    ctx: RunContext[Hypothesis],
    hypothesis: str,
    inputs: dict[str, str],
    steps: dict[str, DraftStep],
) -> Hypothesis:
    """Submit your hypothesis and the DAG. If the DAG is not valid, you get the error.

    Args:
        hypothesis: How this DAG meets the goal: what each step does and why.
        inputs: Maps each DAG input name to its kind. Must be the goal's inputs.
        steps: The steps, keyed by name. A key must not contain ``.``.
    """
    kinds = ctx.deps.input_kinds()
    if inputs != kinds:
        raise ModelRetry(f"inputs must be exactly the goal's inputs: {kinds}")
    try:
        dag = Dag.model_validate(
            {"inputs": inputs, "steps": {k: s.model_dump() for k, s in steps.items()}}
        )
    except ValidationError as e:
        raise ModelRetry(str(e)) from e
    return ctx.deps.model_copy(update={"hypothesis": hypothesis, "dag": dag})


BUILD_INSTRUCTIONS = f"""\
Build a DAG of nodes that meets the user's goal.
1. Call list_nodes.
2. Call describe_node for each node you will use. Use only the fields its schema declares.
3. Call submit_dag with your hypothesis: how the DAG meets the goal. Connect each input port of a step to a source whose kind is the
   kind of that port. A tool step's output is its key. A decision step's outputs are
   <step>.yes and <step>.no.
Known kinds: {sorted(TYPES)}."""

VERIFY_INSTRUCTIONS = """\
You get a hypothesis as JSON: a goal, its inputs, the builder's plan to meet the goal
(hypothesis), the DAG that ran, and the outcome. The outcome holds every value the DAG
produced, keyed by step. Answer: did this workflow
complete its goal? Work out the expected result from the goal and the inputs yourself.
Do not trust the hypothesis or the DAG to be correct. Then compare it with the outcome. Set achieved to
true only if the outcome holds the expected result for every input."""


def build_agent(model: Model | str) -> Agent[Hypothesis, Hypothesis]:
    """Return an agent that writes a hypothesis and a validated Dag for a goal.

    Run it with ``deps=`` the Hypothesis. It returns a copy with ``hypothesis`` and
    ``dag`` set. The DAG must declare exactly the Hypothesis's inputs.
    """
    return Agent(
        model,
        deps_type=Hypothesis,
        instructions=BUILD_INSTRUCTIONS,
        tools=[Tool(list_nodes), Tool(describe_node)],
        output_type=submit_dag,
        retries={"output": 3},
    )


def verify_agent(model: Model | str) -> Agent[None, Verdict]:
    """Return an agent that judges whether a run Hypothesis met its goal."""
    return Agent(model, instructions=VERIFY_INSTRUCTIONS, output_type=Verdict)
