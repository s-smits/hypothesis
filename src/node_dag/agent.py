import json
import uuid
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator
from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.models import Model

from node_dag.dag import Dag, DagOutput
from node_dag.factory import MAPPING, NodeConfig
from node_dag.registry import Registry
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
        goal: What the DAG must do, in plain English, e.g. "lower the atom count of the sequences".
        inputs: The list of entities to run on, keyed by DAG input name. Each list is
            not empty and holds one kind.
        hypothesis: How the builder agent will build and run a DAG to meet ``goal``.
        dag: The DAG the builder agent made.
        workflow_id: The ID of the DagWorkflow run that ran ``dag``.
        outcome: The result of running ``dag`` on ``inputs``.
        verdict: Whether ``outcome`` meets ``goal``.
    """

    id: str = Field(default_factory=lambda: f"hypothesis-{uuid.uuid4()}")
    goal: str
    inputs: dict[str, list[Value]]
    hypothesis: str | None = None
    dag: Dag | None = None
    workflow_id: str | None = None
    outcome: DagOutput | None = None
    verdict: Verdict | None = None

    @field_validator("inputs")
    @classmethod
    def _check_inputs(cls, v: dict[str, list[Value]]) -> dict[str, list[Value]]:
        for name, items in v.items():
            if not items or len({i.kind for i in items}) != 1:
                raise ValueError(
                    f"Input {name!r} must be a list of one kind, not empty"
                )
        return v

    def input_kinds(self) -> dict[str, str]:
        """The DAG inputs the builder must declare: name to the kind of its entities."""
        return {k: v[0].kind for k, v in self.inputs.items()}

    def describe_inputs(self, limit: int = 20) -> dict[str, dict[str, Any]]:
        """What the builder is shown of each input: its kind, size and first entities.

        The builder needs the values themselves to fill in config fields such as a
        reference sequence or a threshold.
        """
        return {
            k: {
                "kind": v[0].kind,
                "count": len(v),
                "sequences": [i.sequence for i in v[:limit]],
            }
            for k, v in self.inputs.items()
        }


class DraftStep(BaseModel):
    """One step of a DAG draft.

    Args:
        node: The id of a registered node, ``<node name>__<config hash>``, from
            create_node or list_registry. Steps may share a node.
        inputs: Maps the node's input port to a source: a DAG input name, a tool or
            scoring step key, or ``<filter step>.yes`` / ``<filter step>.no``.
    """

    node: str
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


BUILD_INSTRUCTIONS = f"""\
Build a DAG of nodes that meets the user's goal. You make the nodes one at a time, in a
registry, then wire them together. You are shown the input sequences. A config field
such as a reference sequence or a threshold must be a real value: copy or work it out
from those inputs, never a placeholder.
1. Call list_nodes for the kinds of node, and list_registry for nodes that are already
   made. Reuse a registered node when it does what you need.
2. Call describe_node for each kind you will make. Use only the fields its schema declares.
3. Call create_node for each node you need, with a short description of what it is for.
   Leave config_hash out: it is set for you. The reply shows the node's id, its input
   port and kind, its outputs, and, for a scorer, the full name of every score column it
   adds. Make a scorer before the filter that reads its column, and copy the column name
   from the scorer's reply into the filter's `column`.
4. Call submit_dag with your hypothesis: how the DAG meets the goal. Each step names a
   registered node by id. Connect its input port to a source whose kind is the kind of
   that port.
Every source is a list of entities, and a node runs once on the whole list that reaches it.
- A tool step makes new entities, under its key. They have no scores.
- A scoring step passes its entities on under its key, and adds its score columns.
- A filter step splits its entities into <step>.yes and <step>.no by its `column`.
Known kinds: {sorted(TYPES)}."""

VERIFY_INSTRUCTIONS = """\
You get a hypothesis as JSON: a goal, its inputs, the builder's plan to meet the goal
(hypothesis), the DAG that ran, and the outcome. The outcome holds every value the DAG
produced, keyed by step. Answer: did this workflow
complete its goal? Work out the expected result from the goal and the inputs yourself.
Do not trust the hypothesis or the DAG to be correct. Then compare it with the outcome. Set achieved to
true only if the outcome holds the expected result for every input."""


def build_agent(
    model: Model | str, registry: Registry
) -> Agent[Hypothesis, Hypothesis]:
    """Return an agent that writes a hypothesis and a validated Dag for a goal.

    The agent makes the nodes it needs in ``registry``, one by one with create_node,
    and can reuse the ones already there. Its DAG steps name registered nodes.

    Run it with ``deps=`` the Hypothesis. It returns a copy with ``hypothesis`` and
    ``dag`` set. The DAG must declare exactly the Hypothesis's inputs.
    """
    adapter: TypeAdapter[NodeConfig] = TypeAdapter(NodeConfig)

    def create_node(config: dict[str, Any], description: str) -> dict[str, Any]:
        """Make a node and add it to the registry. Make nodes one at a time.

        Returns the node's id, config, input port, outputs and score columns. If this
        config is already registered, returns that node with ``new`` false.

        Args:
            config: ``{"name": <node name>, <field>: <value>, ...}``. Read the fields
                with describe_node. Leave out config_hash.
            description: What this node is for in this DAG, in a sentence.
        """
        try:
            node, new = registry.register(adapter.validate_python(config), description)
        except (ValidationError, ValueError) as e:
            raise ModelRetry(str(e)) from e
        return {**node.summary(), "new": new}

    def list_registry() -> list[dict[str, Any]]:
        """List every registered node: id, description, config, input, outputs, score columns."""
        return [n.summary() for n in registry.all()]

    def submit_dag(
        ctx: RunContext[Hypothesis],
        hypothesis: str,
        inputs: dict[str, str],
        steps: dict[str, DraftStep],
    ) -> Hypothesis:
        """Submit your hypothesis and the DAG. If the DAG is not valid, you get the error.

        Args:
            hypothesis: How this DAG meets the goal: what each step does and why.
            inputs: Maps each DAG input name to the kind of its entities. Must be the goal's inputs.
            steps: The steps, keyed by name. A key must not contain ``.``.
        """
        kinds = ctx.deps.input_kinds()
        if inputs != kinds:
            raise ModelRetry(f"inputs must be exactly the goal's inputs: {kinds}")
        nodes = {n.id: n for n in registry.all()}
        if unknown := sorted({s.node for s in steps.values()} - nodes.keys()):
            raise ModelRetry(
                f"Not registered: {unknown}. Make them with create_node. "
                f"Registered: {sorted(nodes)}"
            )
        try:
            dag = Dag.model_validate(
                {
                    "inputs": inputs,
                    "steps": {
                        k: {
                            "config": nodes[s.node].config.model_dump(mode="json"),
                            "inputs": s.inputs,
                        }
                        for k, s in steps.items()
                    },
                }
            )
        except ValidationError as e:
            raise ModelRetry(str(e)) from e
        return ctx.deps.model_copy(update={"hypothesis": hypothesis, "dag": dag})

    return Agent(
        model,
        deps_type=Hypothesis,
        instructions=BUILD_INSTRUCTIONS,
        tools=[
            Tool(list_nodes),
            Tool(describe_node),
            Tool(list_registry),
            Tool(create_node),
        ],
        output_type=submit_dag,
        retries={"output": 3},
    )


def verify_agent(model: Model | str) -> Agent[None, Verdict]:
    """Return an agent that judges whether a run Hypothesis met its goal."""
    return Agent(model, instructions=VERIFY_INSTRUCTIONS, output_type=Verdict)
