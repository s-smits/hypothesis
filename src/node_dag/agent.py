import json
import uuid
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator
from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.models import Model

from node_dag.dag import Dag, DagOutput
from node_dag.factory import MAPPING, NodeConfig
from node_dag.nodes.base import BaseFilterConfig, Category
from node_dag.registry import Registry
from node_dag.types import TYPES, Entity, Value

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


def search_nodes(
    query: str = "",
    input_type: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Search available nodes by intent query and filter by input entity type and category.

    Args:
        query: Words or phrase describing what you want to do (e.g. "score expression", "mutate", "lower atoms", "translate").
        input_type: Input entity kind to filter by ('dna', 'rna', 'amino_acid_sequence', 'protein_structure', 'entity').
        category: Node category to filter by ('scoring', 'filter', 'generation', 'conversion').

    Returns a list of matching nodes with their intents, when to use them, and input/output contracts.
    """
    results: list[dict[str, Any]] = []
    query_tokens = [w.lower() for w in query.split()] if query else []

    for name, node_cls in sorted(NODES.items()):
        contract = node_cls.contract()
        cat_values = contract["categories"]

        # A port takes its kind and every kind under it, e.g. nucleic_acid takes dna.
        want = TYPES.get(input_type.lower(), Entity) if input_type else None
        if want and not issubclass(want, node_cls.port()[1]):
            continue

        if category and category.lower() not in cat_values:
            continue

        score = 0
        if query_tokens:
            intents_text = " ".join(contract.get("intents", [])).lower()
            when_text = contract.get("when_to_use", "").lower()
            doc_text = (node_cls.__doc__ or "").lower()
            name_text = name.lower()

            for token in query_tokens:
                if token in name_text:
                    score += 5
                if token in intents_text:
                    score += 3
                if token in when_text:
                    score += 2
                if token in doc_text:
                    score += 1
                if token in cat_values:
                    score += 2
            if score == 0:
                continue

        results.append({
            "score": score,
            "data": {
                "name": name,
                "categories": contract["categories"],
                "input": contract["inputs"],
                "outputs": contract["outputs"],
                "intents": contract.get("intents", []),
                "when_to_use": contract.get("when_to_use", ""),
                "when_not_to_use": contract.get("when_not_to_use", ""),
            },
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return [r["data"] for r in results]


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

Match the DAG to the goal's archetype:
- Measurement Archetype: Goal asks to measure, score, or convert given sequences
  (e.g. "Score sequence via ostir expression", "Convert DNA to protein").
  Topology: Input -> [Scorer | Converter].
- Screening Archetype: Goal asks to filter existing sequences against a threshold.
  Topology: Input -> Scorer -> Filter(threshold).
- Optimization / Search Archetype: Goal asks to find, produce, reduce, increase, or
  improve a metric (e.g. "find sequences with higher ostir expression", "lower atom count by 1").
  Topology: Input -> Generator (e.g. mutate_synonymous with variants_per_sequence > 1) ->
            Scorer -> Filter(threshold beating input baseline).
  RULES FOR OPTIMIZATION:
  1. You MUST generate candidate variants using a generation node (mutate_synonymous).
     Scoring and filtering alone cannot find what the inputs do not already hold!
  2. Increase variants_per_sequence (e.g. 10) on mutate_synonymous so a candidate pool
     is created for selection.
  3. Work out the input's baseline score, and set the filter threshold strictly relative
     to that baseline (e.g. threshold < baseline for fewer atoms; threshold > baseline
     for higher expression). A threshold that every entity passes is not a filter, and a
     DAG that keeps everything has chosen nothing.

Steps:
1. Call search_nodes (with query and input_type) or list_nodes to find nodes by intent.
   Call list_registry for nodes already made. Reuse a registered node when it does what
   you need.
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
true only if the outcome holds the expected result for every input.
Set achieved to false, whatever else the DAG did, when:
- The goal names a measure, a method or a node that the DAG did not use. A different
  node is not a substitute, and the hypothesis calling it one does not make it one.
- The goal asks to find or choose something, and the DAG chose nothing: it returns its
  inputs unchanged, or every entity passed its filter, or none did.
- The goal asks for higher, lower, more, less, or optimized values, but the outcome only
  holds the original input sequence(s) without improvement or with a filter threshold
  that trivialized selection (e.g. threshold 0.0 on positive scores).
- A threshold let everything through, so the filter decided nothing."""


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

        # Semantic check: optimization goals with 1 input sequence must generate candidates
        comparative_words = (
            "higher",
            "lower",
            "reduce",
            "decrease",
            "increase",
            "fewer",
            "less",
            "more",
            "optimize",
            "better",
            "beat",
            "minimize",
            "maximize",
        )
        goal_lower = ctx.deps.goal.lower()
        is_comparative = any(w in goal_lower for w in comparative_words)
        total_input_count = sum(len(v) for v in ctx.deps.inputs.values())

        if is_comparative and total_input_count == 1:
            has_generation = any(
                "generation" in [c.value for c in nodes[s.node].config.categories]
                for s in steps.values()
            )
            if not has_generation:
                raise ModelRetry(
                    f"The goal asks to optimize or find improved sequences ({ctx.deps.goal!r}), "
                    "but only 1 input sequence was provided and the DAG has no generation node "
                    "(e.g. mutate_synonymous) to create candidate variants. A DAG that only "
                    "measures the input cannot find sequences with higher/lower metrics. "
                    "Add a generation step (e.g. mutate_synonymous with variants_per_sequence > 1) "
                    "to generate variants, score them, and filter for those that beat the baseline."
                )

        # Semantic check: reject trivial filter thresholds like 0.0 on positive scores
        for step_name, s in steps.items():
            cfg = nodes[s.node].config
            if isinstance(cfg, BaseFilterConfig):
                if (
                    cfg.name == "at_least"
                    and "expression" in cfg.column
                    and cfg.threshold <= 0.0
                ):
                    raise ModelRetry(
                        f"Step {step_name!r} filters on expression with threshold {cfg.threshold}, "
                        "which allows every sequence to pass trivially. Determine the baseline score "
                        "and set a threshold that requires candidates to beat the baseline."
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
            Tool(search_nodes),
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
