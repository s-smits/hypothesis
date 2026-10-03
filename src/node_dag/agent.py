import json
import uuid
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_ai import Agent, ModelRetry, RunContext, Tool, ToolOutput
from pydantic_ai.models import Model

from node_dag.factory import MAPPING, NodeConfig
from node_dag.nodes.base import BaseFilterConfig, BaseNodeConfig
from node_dag.plan import (
    Attempt,
    Criterion,
    Critique,
    HypothesisState,
    Plan,
    ToolRequest,
    VerifyOpinion,
    repeated,
)
from node_dag.registry import Registry
from node_dag.types import TYPES, Entity, Value

NODES = {c.model_fields["name"].default: c for c in MAPPING}
# What a file from before the loop kept on the Hypothesis, and each Attempt keeps now.
TOP_LEVEL_RUN = ("dag", "workflow_id", "outcome", "verdict")


class Hypothesis(BaseModel):
    """A goal, and the plans tried against it, round by round.

    Set ``goal`` and ``inputs``, and ``criteria`` or ``hypothesis`` if you have them.
    ``HypothesisLoop`` fills in the rest: ``attempts`` holds each round's plan, DAG,
    outcome and verdict, and ``current`` is the one in progress.

    Args:
        id: Names the saved file. Generated if not given.
        goal: What the DAG must do, in plain English, e.g. "lower the atom count of the sequences".
        inputs: The list of entities to run on, keyed by DAG input name. Each list is
            not empty and holds one kind.
        hypothesis: Your own idea of how to meet ``goal``, for the builder to take or leave.
        criteria: What must be true for the goal to be met. Frozen before any plan.
        state: Where the loop has got to. None for a file from before the loop.
        round: The current round.
        attempts: Every round so far. Older rounds keep previews, not their outcome.
        usage: Tokens used, per stage and in ``total``.
        stopped_because: Why the loop ended.
    """

    id: str = Field(default_factory=lambda: f"hypothesis-{uuid.uuid4()}")
    goal: str
    inputs: dict[str, list[Value]]
    hypothesis: str | None = None
    criteria: list[Criterion] = []
    state: HypothesisState | None = None
    round: int = 0
    attempts: list[Attempt] = []
    usage: dict[str, int] = {}
    stopped_because: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_before_the_loop(cls, data: Any) -> Any:  # noqa: ANN401
        """A file from before the loop kept its one run at the top: make it round 1."""
        if not isinstance(data, dict) or data.get("attempts"):
            return data
        # A copy: a before-validator must not change its caller's input.
        data = dict(data)
        run = {k: data.pop(k) for k in TOP_LEVEL_RUN if data.get(k)}
        if run:
            data |= {"round": 1, "attempts": [{"round": 1, **run}]}
        return data

    @field_validator("criteria")
    @classmethod
    def _check_criteria(cls, v: list[Criterion]) -> list[Criterion]:
        if dup := repeated(v):
            raise ValueError(f"Criterion ids must be unique; these repeat: {dup}")
        return v

    @field_validator("inputs")
    @classmethod
    def _check_inputs(cls, v: dict[str, list[Value]]) -> dict[str, list[Value]]:
        for name, items in v.items():
            if not items or len({i.kind for i in items}) != 1:
                raise ValueError(
                    f"Input {name!r} must be a list of one kind, not empty"
                )
        return v

    @property
    def current(self) -> Attempt | None:
        """This round's attempt. None while the builder is still planning it."""
        return next((a for a in self.attempts if a.round == self.round), None)

    @property
    def pending(self) -> list[ToolRequest]:
        """The nodes a blocked run is waiting for."""
        cur = self.current
        return cur.requests if cur and self.state == "blocked" else []

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

        results.append(
            {
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
            }
        )

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
Plan a DAG of nodes that meets the user's goal. You are shown the input sequences and the
criteria you will be marked against, which you cannot change. A config field such as a
reference sequence or a threshold must be a real value from those inputs, never a placeholder.

Shapes that usually fit a goal:
- Measure or convert given sequences: input -> scorer or converter.
- Screen sequences against a threshold: input -> scorer -> filter.
- Find, improve, raise or lower something: input -> generator (e.g. mutate_synonymous or
  recode_codons) -> scorer -> filter. Scoring and filtering alone cannot find what the
  inputs do not already hold. Work out the inputs' baseline score and set the threshold
  relative to it: a threshold every entity passes decides nothing.

1. Call search_nodes (by intent and input_type) or list_nodes, list_registry for nodes
   already made, and describe_node for each kind you use.
2. Each step names a registered node id (from create_node), or a node name with its fields
   in `config`. Connect its input port to a source whose kind is the kind of that port.
   Register a scorer with create_node before the filter on its column, and copy the column
   name from the reply.
3. Every criterion needs an assertion: a filter step and the branch, yes or no, that proves
   it. The assertion holds only if that branch took every entity and the other took none.
4. If no existing node can do a step, put its contract in `requests`, keyed by name, and use
   that name in a step. Name the existing nodes you considered in why_not_composable. A
   filter on a requested scorer's column gets that column name from the error you are shown.
5. After a rejected round, say in addresses_critique what changed, and do not resubmit a
   wiring that already ran.
Every source is a list of entities, and a node runs once on the whole list that reaches it.
- A tool step makes new entities, under its key. They have no scores.
- A scoring step passes its entities on under its key, and adds its score columns.
- A filter step splits its entities into <step>.yes and <step>.no by its `column`.
Known kinds: {sorted(TYPES)}."""

VERIFY_INSTRUCTIONS = """\
You get JSON: a goal, its criteria and inputs, the plan (hypothesis, expected, assertions),
`held` (whether each assertion's branch fired, worked out by code), the DAG and the outcome.
Work out the expected result from the goal and inputs yourself, and do not trust the plan.
Set agrees to true only if the outcome holds the expected result for every input. Set
covers_goal to true only if the assertions genuinely test every criterion. You cannot
declare success: false is a veto and true grants nothing.
Set agrees to false, whatever else the DAG did, when:
- The goal names a measure, a method or a node that the DAG did not use. A different
  node is not a substitute, and the hypothesis calling it one does not make it one.
- The goal asks to find or choose something, and the DAG chose nothing: it returns its
  inputs unchanged, or every entity passed its filter, or none did.
- The goal asks for higher, lower, more, less or optimized values, but the outcome holds
  only the original inputs, without improvement.
- A threshold let everything through, so the filter decided nothing."""

CRITIQUE_INSTRUCTIONS = """\
You get a round that missed its goal as JSON: the plan, the outcome, the verdict and earlier
rounds. Say what went wrong in terms of its steps and values, the one root cause, which
step keys were right (keep), and what the next plan must do differently. Do not send it
back to a wiring an earlier round already ran."""

CRITERIA_INSTRUCTIONS = """\
Turn the goal into one to four criteria that decide whether it was met. Each is a claim a
filter over the DAG's output could check. State what must be true, not how to do it."""

MAX_REQUESTS = 3
ADAPTER: TypeAdapter[NodeConfig] = TypeAdapter(NodeConfig)


def step_config(plan: Plan, key: str, registry: Registry) -> BaseNodeConfig:
    """The config of step ``key``: registered, an existing node, or a requested stand-in."""
    s = plan.steps[key]
    if node := registry.get(s.node):
        return node.config
    if s.node in plan.requests and s.node not in NODES:
        return plan.requests[s.node].stand_in()(**s.config)
    return ADAPTER.validate_python({"name": s.node, **s.config})


def plan_prompt(hyp: Hypothesis) -> str:
    """What the builder reasons from: goal, inputs, criteria, and every attempt so far."""
    parts = [f"Goal: {hyp.goal}", f"Inputs: {json.dumps(hyp.describe_inputs())}"]
    parts.append(
        "Criteria (each needs an assertion): "
        + json.dumps({c.id: c.claim for c in hyp.criteria})
    )
    if hyp.hypothesis:
        parts.append(f"The user's own idea, to take or leave: {hyp.hypothesis}")
    if hyp.attempts:
        parts.append(
            "Attempts so far:\n"
            + json.dumps([a.summary() for a in hyp.attempts], indent=1)
        )
    return "\n\n".join(parts)


def build_agent(model: Model | str, registry: Registry) -> Agent[Hypothesis, Plan]:
    """Return an agent that writes a Plan for a goal, with each guard a retry.

    The agent makes the nodes it needs in ``registry`` with create_node, and can reuse
    the ones already there. Run it with ``deps=`` the Hypothesis, whose ``criteria`` and
    ``attempts`` the guards read. A plan may name a node that does not exist, if it
    carries a ToolRequest for it; the plan is typechecked as if the node were written.
    """

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
            node, new = registry.register(ADAPTER.validate_python(config), description)
        except (ValidationError, ValueError) as e:
            raise ModelRetry(str(e)) from e
        return {**node.summary(), "new": new}

    def list_registry() -> list[dict[str, Any]]:
        """List every registered node: id, description, config, input, outputs, score columns."""
        return [n.summary() for n in registry.all()]

    def check_plan(ctx: RunContext[Hypothesis], plan: Plan) -> Plan:
        hyp, reqs = ctx.deps, plan.requests
        if plan.inputs != hyp.input_kinds():
            raise ModelRetry(
                f"inputs must be exactly the goal's inputs: {hyp.input_kinds()}"
            )
        used = {s.node for s in plan.steps.values()}
        if len(reqs) > MAX_REQUESTS or (reqs and used <= reqs.keys()):
            raise ModelRetry(
                f"Request at most {MAX_REQUESTS} nodes, and not for every step."
            )
        if unused := sorted(reqs.keys() - used):
            raise ModelRetry(f"Requests no step uses: {unused}")
        for r in reqs.values():
            if r.name in NODES:
                raise ModelRetry(f"{r.name} already exists: use it, do not request it.")
            if not any(n in r.why_not_composable for n in NODES):
                raise ModelRetry(
                    f"{r.name}: why_not_composable must name the existing nodes you considered: {sorted(NODES)}"
                )
        try:
            configs = {k: step_config(plan, k, registry) for k in plan.steps}
            plan.typecheck(configs)
        except (ValidationError, ValueError, TypeError) as e:
            raise ModelRetry(str(e)) from e
        ids = {c.id for c in hyp.criteria}
        for a in plan.assertions:
            if a.criterion not in ids:
                raise ModelRetry(
                    f"Assertion on unknown criterion {a.criterion!r}; criteria: {sorted(ids)}"
                )
            if not isinstance(configs.get(a.step), BaseFilterConfig):
                raise ModelRetry(f"Assertion step {a.step!r} must be a filter step.")
        if uncovered := sorted(ids - {a.criterion for a in plan.assertions}):
            raise ModelRetry(f"No assertion covers {uncovered}.")
        last = hyp.attempts[-1] if hyp.attempts else None
        if plan.fingerprint() in {a.plan.fingerprint() for a in hyp.attempts if a.plan}:
            raise ModelRetry("This wiring already ran in an earlier round. Change it.")
        if last and last.critique and not plan.addresses_critique:
            raise ModelRetry(
                "Say in addresses_critique what this plan changes in response to the critique."
            )
        return plan

    agent = Agent(
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
        # Not strict: strict output sets additionalProperties false on every object,
        # which leaves steps, inputs, config and requests only able to be {}.
        output_type=ToolOutput(Plan, strict=False),
        retries={"output": 3},
    )
    agent.output_validator(check_plan)
    return agent


def verify_agent(model: Model | str) -> Agent[None, VerifyOpinion]:
    """Return an agent that judges a round. Its opinion can veto, never grant."""
    return Agent(model, instructions=VERIFY_INSTRUCTIONS, output_type=VerifyOpinion)


def critique_agent(model: Model | str) -> Agent[None, Critique]:
    """Return an agent that says why a round missed and what the next plan must change."""
    return Agent(model, instructions=CRITIQUE_INSTRUCTIONS, output_type=Critique)


def criteria_agent(model: Model | str) -> Agent[None, list[Criterion]]:
    """Return an agent that turns a goal into the criteria that decide whether it was met."""
    agent = Agent(
        model, instructions=CRITERIA_INSTRUCTIONS, output_type=list[Criterion]
    )

    @agent.output_validator
    def unique(criteria: list[Criterion]) -> list[Criterion]:
        if dup := repeated(criteria):
            raise ModelRetry(f"Criterion ids must be unique; these repeat: {dup}.")
        return criteria

    return agent
