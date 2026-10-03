"""The agents that plan, judge and critique.

Four agents, three jobs. The builder writes a :class:`~node_dag.plan.Plan` naming the
best nodes for the goal **whether or not they exist**; the verifier offers an opinion it
cannot turn into success on its own; the critic says what to change. The criteria agent
runs once, before any plan, and what it writes is frozen — the builder may choose how to
pass the exam but never what the exam asks.

The guards in :func:`submit_plan` are the whole reason the first of those is safe. An
agent allowed to invent tools will invent its way out of thinking unless inventing one
costs more precision than using an existing one.
"""

import json
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import Model

from node_dag.dag import Dag
from node_dag.factory import MAPPING
from node_dag.plan import (
    Assertion,
    Criterion,
    Critique,
    Hypothesis,
    Plan,
    PlannedStep,
    ToolRequest,
    Verdict,
    VerifyOpinion,
)
from node_dag.types import TYPES
from node_dag.wiring import PortContract, check_wiring

NODES = {c.model_fields["name"].default: c for c in MAPPING}

# Re-exported: Hypothesis and Verdict moved to node_dag.plan so the Temporal workflow can
# import them without dragging pydantic_ai into the sandbox.
__all__ = [
    "NODES",
    "Criterion",
    "Critique",
    "Hypothesis",
    "Plan",
    "Verdict",
    "VerifyOpinion",
    "criteria_agent",
    "critique_agent",
    "describe_node",
    "list_nodes",
    "plan_agent",
    "submit_plan",
    "verdict_agent",
]


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


class PlanDeps(BaseModel):
    """What ``submit_plan`` checks a submission against.

    Deliberately not the whole Hypothesis: the builder's output function should not be
    able to mutate the run it is planning for. The workflow owns the Hypothesis; this
    agent owns only its Plan.

    Args:
        goal: What the plan must achieve.
        criteria: What must be true for the goal to be met. Frozen before this agent ran.
        input_kinds: Each DAG input name to the kind it must be declared as.
        max_requests: How many new tools one plan may ask for.
        tried: Wiring fingerprints already run, so a round cannot resubmit an old graph.
        has_critique: Whether a critique was supplied, so a plan must say what it changes.
    """

    goal: str
    criteria: list[Criterion] = []
    input_kinds: dict[str, str]
    max_requests: int = 2
    tried: list[str] = []
    has_critique: bool = False


def _called(ctx: RunContext[PlanDeps]) -> list[ToolCallPart]:
    """Every tool call the builder made this run, in order."""
    return [
        p
        for m in ctx.messages
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolCallPart)
    ]


def _near(req: ToolRequest) -> list[str]:
    """Existing nodes that might already do what ``req`` asks for.

    Three cheap deterministic signals, no model and no embeddings: the same port kinds
    and output, an output this request could be built on, or an overlapping name. A hit
    is an argument to answer, not a refusal.

    Args:
        req: The contract the builder asked for.
    """
    want, hits = req.contract(), []
    for name, cfg in NODES.items():
        have = PortContract.of(cfg)
        same_shape = (
            sorted(have.inputs.values()) == sorted(want.inputs.values())
            and have.output == want.output
        )
        can_feed = (
            want.output is not None
            and have.output == want.output
            and set(have.inputs.values()) <= set(want.inputs.values())
        )
        mine, theirs = set(req.name.split("_")), set(name.split("_"))
        similar = len(mine & theirs) / len(mine | theirs) >= 0.5
        if same_shape or can_feed or similar:
            hits.append(name)
    return hits


class _Wired:
    """A planned step as a ``wiring.Wired`` view, over a real node or a request."""

    def __init__(self, step: PlannedStep, contract: PortContract) -> None:
        """Keep the step's sources and the contract of whatever node it names."""
        self.inputs = step.inputs
        self._contract = contract

    def contract(self) -> PortContract:
        """The ports and kinds of the node this step runs."""
        return self._contract


def _contracts(
    steps: dict[str, PlannedStep], requests: dict[str, ToolRequest]
) -> dict[str, _Wired]:
    """A wired view per step, taking each contract from NODES or from its request.

    Args:
        steps: The plan's steps.
        requests: The contracts for nodes that do not exist, keyed by node name.
    """
    out: dict[str, _Wired] = {}
    for key, step in steps.items():
        if step.node in NODES:
            out[key] = _Wired(step, PortContract.of(NODES[step.node]))
        elif step.node in requests:
            out[key] = _Wired(step, requests[step.node].contract())
        else:
            raise ModelRetry(
                f"Step {key!r} uses node {step.node!r}, which is neither an existing "
                "node nor one of your requests. Use an existing node, or submit a "
                "ToolRequest for it."
            )
    return out


def submit_plan(
    ctx: RunContext[PlanDeps],
    hypothesis: str,
    expected: str,
    inputs: dict[str, str],
    steps: dict[str, PlannedStep],
    assertions: list[Assertion],
    reasons: dict[str, str] | None = None,
    requests: list[ToolRequest] | None = None,
    addresses_critique: str = "",
) -> Plan:
    """Submit your plan. Name the node you need even if nobody has written it yet.

    Args:
        hypothesis: How this plan meets the goal: what each step does and why.
        expected: What the DAG will produce. A prediction you can be wrong about, not a
            restatement of the goal.
        inputs: Each DAG input name to its kind. Must be exactly the goal's inputs.
        steps: The steps, keyed by name. A key must not contain ``.``.
        assertions: One per criterion at least, each naming a decision step of this plan
            whose branch settles it. A plan that asserts nothing can never be accepted.
        reasons: Why you chose each existing node, keyed by node name.
        requests: A full contract for every node you named that does not exist.
        addresses_critique: What this plan changes in response to the critique. Required
            once a critique has been given.
    """
    deps = ctx.deps
    reasons, requests = reasons or {}, requests or []

    # G0: the DAG must run on the inputs the goal was asked about, not ones it invents.
    if inputs != deps.input_kinds:
        raise ModelRetry(f"inputs must be exactly the goal's inputs: {deps.input_kinds}")

    names = {s.node for s in steps.values()}
    by_name = {r.name: r for r in requests}
    called = _called(ctx)
    described = {
        p.args_as_dict().get("name") for p in called if p.tool_name == "describe_node"
    }

    # G1: you cannot know a tool is missing without having read the catalogue.
    if requests and not any(p.tool_name == "list_nodes" for p in called):
        raise ModelRetry(
            "Call list_nodes before asking for a new tool. You cannot know a tool is "
            "missing without reading the catalogue."
        )
    if requests and not described:
        raise ModelRetry(
            "Call describe_node on the nodes you considered, then say in "
            "why_not_composable what each of them cannot do."
        )
    if unread := sorted((names & set(NODES)) - described):
        raise ModelRetry(
            f"Call describe_node for each existing node you use: {unread}. Use only the "
            "fields its schema declares."
        )

    # G2: inventing tools must not become a way to avoid decomposing the problem.
    if len(requests) > deps.max_requests:
        raise ModelRetry(
            f"At most {deps.max_requests} new tools per plan; you asked for "
            f"{len(requests)}: {sorted(by_name)}. Keep the one the goal cannot be met "
            "without, and compose the rest from existing nodes."
        )
    if requests and len(requests) >= len(steps):
        raise ModelRetry(
            "Every step of your plan is a tool that does not exist. That is not a plan, "
            "it is a wish list. Use the catalogue."
        )

    for req in requests:
        # G3: a new tool reads and writes kinds that already exist. Without this the
        # wiring check below is vacuous, because the agent can invent a type too.
        kinds = set(req.inputs.values()) | ({req.output} if req.output else set())
        if bad := sorted(kinds - set(TYPES)):
            raise ModelRetry(
                f"{req.name}: unknown kinds {bad}. A new tool must read and write kinds "
                f"that already exist: {sorted(TYPES)}. You cannot request a new type."
            )
        # G4: do not shadow a node that is already on the shelf.
        if req.name in NODES:
            raise ModelRetry(
                f"{req.name} already exists: {json.dumps(NODES[req.name].contract())}. "
                "Use it."
            )
        # G5: a near match is an argument to answer, not an automatic refusal.
        if (hits := _near(req)) and not any(h in req.why_not_composable for h in hits):
            shapes = {h: NODES[h].contract()["inputs"] for h in hits}
            raise ModelRetry(
                f"{req.name} looks like it overlaps {hits}: {json.dumps(shapes)}. "
                "Either use one of them, or name it in why_not_composable and say "
                "exactly what it cannot do."
            )
        # G6: "no existing tool does this" is not an argument.
        if not any(n in req.why_not_composable for n in NODES):
            raise ModelRetry(
                f"{req.name}: why_not_composable must name at least one existing node "
                "you considered and say what it cannot do."
            )

    if unused := sorted(set(by_name) - names):
        raise ModelRetry(f"You requested {unused} but no step uses them.")

    # G7: the cage comes off for unknown node names. It stays on for known ones.
    for key, step in steps.items():
        if step.node not in NODES:
            continue
        try:
            NODES[step.node].model_validate({"name": step.node, **step.config})
        except ValidationError as e:
            raise ModelRetry(f"Step {key!r} config for {step.node}: {e}") from e

    # G8: a requested tool is not a wildcard. Declare its ports and wire them correctly.
    try:
        check_wiring(inputs, _contracts(steps, by_name))
    except ValueError as e:
        raise ModelRetry(str(e)) from e

    # G9: every choice carries its reason.
    if missing := sorted((names & set(NODES)) - set(reasons)):
        raise ModelRetry(f"Give a reason for each existing node you chose: {missing}.")

    # G10: the assertions are the exam, and the exam was set before you planned.
    wanted = {c.id for c in deps.criteria}
    for a in assertions:
        if a.criterion not in wanted:
            raise ModelRetry(
                f"Assertion names criterion {a.criterion!r}, which is not one of "
                f"{sorted(wanted)}. You cannot add or rename criteria."
            )
        if a.step not in steps:
            raise ModelRetry(
                f"Assertion names step {a.step!r}, which is not in the plan."
            )
        node = steps[a.step].node
        is_decision = (
            node in NODES and PortContract.of(NODES[node]).forwards is not None
        ) or (node in by_name and by_name[node].node == "decision")
        if not is_decision:
            raise ModelRetry(
                f"Assertion names step {a.step!r}, which runs {node!r}, a tool. Only a "
                "decision has a branch to settle a claim. Add a decision step that "
                "checks this, or request one."
            )
    if uncovered := sorted(wanted - {a.criterion for a in assertions}):
        raise ModelRetry(
            f"No assertion covers {uncovered}. Every criterion needs a decision step "
            "whose branch proves it. If no node can check one, request the node that "
            "can: a plan that cannot be checked cannot be accepted."
        )

    plan = Plan(
        hypothesis=hypothesis,
        expected=expected,
        assertions=assertions,
        inputs=inputs,
        steps=steps,
        requests=by_name,
        reasons=reasons,
        addresses_critique=addresses_critique,
    )

    # G11: a later round must change the wiring, and say what it changed.
    if plan.fingerprint() in deps.tried:
        raise ModelRetry(
            "You have already run this exact wiring and it did not meet the goal. "
            "Change the nodes, the configs or the shape; rewording will not do."
        )
    if deps.has_critique and not addresses_critique.strip():
        raise ModelRetry(
            "Say in addresses_critique what this plan changes in response to the "
            "critique you were given."
        )

    # Nothing missing: take the strict path and prove it builds a real Dag.
    if not names - set(NODES):
        try:
            Dag.model_validate(
                {"inputs": inputs, "steps": {k: s.draft() for k, s in steps.items()}}
            )
        except ValidationError as e:
            raise ModelRetry(str(e)) from e
    return plan


PLAN_INSTRUCTIONS = f"""\
Plan a DAG of nodes that meets the user's goal.

1. Call list_nodes, then describe_node for every node you are considering.
2. Design the best plan for the goal. If the best plan needs a node that does not exist,
   name it in a step anyway and submit a full ToolRequest for it. Do NOT settle for a
   worse plan that only uses the nodes on the shelf: a missing tool is a request for a
   human to write it, not a dead end. But do not ask for a tool you have not shown you
   need. Say in why_not_composable which existing nodes you considered and what each
   cannot do.
3. Commit to a prediction. ``expected`` says what the DAG will produce, specifically
   enough to be wrong about. Then back it with assertions: one per criterion at least,
   each naming a decision step of your plan whose yes/no branch settles that criterion.
   The criteria are fixed and you cannot change them. A plan that asserts nothing can
   never be accepted, however good it looks.
4. Give a reason for every node you chose and every step you wired.

Known kinds: {sorted(TYPES)}. You cannot introduce a new kind."""

CRITIQUE_INSTRUCTIONS = """\
An attempt did not meet its goal. Say why, and what the next plan must change.

You get the goal, the criteria, the plan, its prediction, which assertions held, the DAG
and everything it produced. Work from the evidence: name the step keys and values that
show the problem. Pick the single root cause most responsible, not a list of everything
imperfect.

Say what to keep. A plan that was nearly right should not be thrown away over one bad
step, and the next builder is told to honour your keep list.

If the attempt failed because no available node can check or achieve something the goal
needs, say so with root_cause "missing_tool" and name the tool in fix. That is a useful
result, not a failure: it becomes a request for a human to write that node."""

CRITERIA_INSTRUCTIONS = """\
Turn a goal into the criteria that decide whether it was met.

A criterion is one thing that must be true, stated so that a check could settle it: "no
TCG, TCA or TAG codon remains", not "the sequence is improved". Prefer few and sharp over
many and vague.

These are frozen before anything is planned, and the agent that plans cannot change them.
So include what the goal genuinely requires even when you doubt a tool exists to check
it: an unverifiable criterion makes the run ask for the missing checker, which is the
right outcome. Never weaken a criterion to make it easy to pass."""

VERDICT_INSTRUCTIONS = """\
Judge whether a DAG run met its goal. You are a check on the result, not its author.

Ground truth is the DAG's own decision steps. The held map already tells you which
assertions fired; those are settled facts and you must not re-litigate them. Re-derive by
hand only what no check covers, and only when you genuinely can. Do not claim to have
verified a thousand codons by inspection.

Set covers_goal to false when the assertions are superficial: when they pass but do not
actually test the criteria, or when a criterion was judged by eye rather than by a node.
Say so in reason. Admitting that something went unchecked is the most useful thing you
can do, because it becomes a request for the node that would check it.

Your agreement does not by itself make a run successful: acceptance also needs every
criterion covered by an assertion that held. Your disagreement does block it. Judge
accordingly: be strict, and never generous."""


def plan_agent(model: Model | str) -> Agent[PlanDeps, Plan]:
    """Return an agent that plans a DAG, naming tools whether or not they exist.

    Run it with ``deps=`` a PlanDeps. The guards in ``submit_plan`` come back to the
    model as retries, so a rejected plan is a conversation rather than a failure.

    Args:
        model: The pydantic-ai model to plan with.
    """
    return Agent(
        model,
        deps_type=PlanDeps,
        instructions=PLAN_INSTRUCTIONS,
        tools=[Tool(list_nodes), Tool(describe_node)],
        output_type=submit_plan,
        retries={"output": 3},
    )


def critique_agent(model: Model | str) -> Agent[None, Critique]:
    """Return an agent that says why an attempt missed and what to change.

    Args:
        model: The pydantic-ai model to critique with.
    """
    return Agent(model, instructions=CRITIQUE_INSTRUCTIONS, output_type=Critique)


def criteria_agent(model: Model | str) -> Agent[None, list[Criterion]]:
    """Return an agent that turns a goal into the criteria that decide it.

    Runs once, before any plan. What it writes is frozen, so the builder may choose how
    to pass the exam but never what it asks.

    Args:
        model: The pydantic-ai model to derive criteria with.
    """
    return Agent(model, instructions=CRITERIA_INSTRUCTIONS, output_type=list[Criterion])


def verdict_agent(model: Model | str) -> Agent[None, VerifyOpinion]:
    """Return an agent that judges a run, and cannot declare it successful.

    Its output type has no ``achieved`` field: the workflow computes that from the
    criteria and the assertions. A model may veto a result, never certify one.

    Args:
        model: The pydantic-ai model to judge with.
    """
    return Agent(model, instructions=VERDICT_INSTRUCTIONS, output_type=VerifyOpinion)
