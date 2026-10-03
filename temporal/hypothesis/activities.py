"""The activities the hypothesis loop runs outside its workflow.

Everything non-deterministic lives here: the model calls, and every read of the node
registry. That second one is the reason this module exists rather than the workflow
calling the agents directly. ``NODES`` is built when ``node_dag.factory`` is imported, and
the whole point of the blocked state is that it *changes* under a running workflow when
someone adds a node and restarts the worker. A registry read in workflow code would
diverge on replay; behind an activity boundary its answer is recorded in history.

Agent activities never raise on model misbehaviour. A model that cannot produce a usable
answer returns a structured ``error``, which becomes an input to the critique rather than
a dead workflow.
"""

import hashlib
import json
from typing import Any

from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ToolCallPart
from temporalio import activity

from node_dag.agent import (
    NODES,
    PlanDeps,
    criteria_agent,
    critique_agent,
    plan_agent,
    verdict_agent,
)
from node_dag.dag import Dag
from node_dag.plan import Verdict
from node_dag.wiring import PortContract
from temporal.dag.activities import write_atomic
from temporal.hypothesis.models import (
    CriteriaInput,
    CriteriaOutput,
    CritiqueInput,
    CritiqueOutput,
    PlanInput,
    PlanOutput,
    ResolveInput,
    ResolveOutput,
    SaveHypothesisInput,
    SaveRequestsInput,
    VerifyInput,
)
from temporal.store import save_hypothesis, save_tool_request, trajectories_dir

#: A refusal is the provider declining, not a wobble. Another identical request gets
#: an identical answer, so a loop that retries one only spends money to learn that.
_REFUSALS = ("content filter", "refusal", "safety")


def _refused(message: str) -> bool:
    """Whether a model failure is one that retrying cannot fix."""
    low = message.lower()
    return any(m in low for m in _REFUSALS)


def _brief(message: str, limit: int = 300) -> str:
    """The first line of a model failure, without the provider's response body.

    A refusal arrives with the whole response attached, including base64 thinking
    signatures. Pasted into a page meant to say what went wrong, it buries it.
    """
    head = message.split(", body:", 1)[0].splitlines()[0].strip()
    return head if len(head) <= limit else head[: limit - 1] + "…"


def _record(tag: str, result: Any) -> tuple[str | None, int]:  # noqa: ANN401
    """Write an agent run's message history to disk and report its token use.

    The history stays on disk and only its path crosses the workflow boundary: a few
    rounds of full transcripts would run into Temporal's 2 MB payload limit, and the
    workflow has no use for them. Keeping them at all is what makes a plan auditable --
    which node schemas the builder read, what it tried first, which guard it bounced off.

    Args:
        tag: Names the file, e.g. ``<hypothesis id>-r2-plan``.
        result: The pydantic-ai run result.
    """
    tokens = 0
    try:
        usage = result.usage()
        tokens = getattr(usage, "total_tokens", 0) or (
            getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)
        )
    except Exception:  # noqa: BLE001 - usage accounting must never fail a run
        tokens = 0
    if not tag:
        return None, tokens
    path = trajectories_dir() / f"{tag}.json"
    try:
        write_atomic(path, result.all_messages_json())
    except Exception:  # noqa: BLE001 - an unwritable transcript must not fail the run
        return None, tokens
    return str(path), tokens


@activity.defn
async def derive_criteria(inp: CriteriaInput) -> CriteriaOutput:
    """Turn a goal into the criteria that decide whether it was met.

    Runs once, before any plan exists, and what it returns is frozen for the rest of the
    investigation. That ordering is the point: the agent that plans cannot choose what it
    will be marked against.

    Args:
        inp: The goal, its input kinds, and the model to use.
    """
    agent = criteria_agent(inp.model)
    prompt = f"Goal: {inp.goal}\nInputs (name: kind): {inp.input_kinds}"
    try:
        result = await agent.run(prompt)
    except UnexpectedModelBehavior as e:
        return CriteriaOutput(error=_brief(str(e)), terminal=_refused(str(e)))
    path, tokens = _record(inp.tag, result)
    return CriteriaOutput(criteria=result.output, trajectory=path, tokens=tokens)


@activity.defn
async def plan_hypothesis(inp: PlanInput) -> PlanOutput:
    """Run the builder agent and return its plan, or why it could not make one.

    Args:
        inp: The goal, the frozen criteria, the critique, and every previous attempt.
    """
    deps = PlanDeps(
        goal=inp.goal,
        criteria=inp.criteria,
        input_kinds=inp.input_kinds,
        max_requests=inp.max_requests,
        tried=[h.fingerprint for h in inp.history if h.fingerprint],
        has_critique=inp.critique is not None,
    )
    agent = plan_agent(inp.model)
    try:
        result = await agent.run(_plan_prompt(inp), deps=deps)
    except UnexpectedModelBehavior as e:
        # Out of output retries: the guards kept rejecting it. That is a critique input,
        # not a crash -- the loop should get a chance to diagnose and try again. Unless
        # the provider refused, which no amount of trying again will change.
        refused = _refused(str(e))
        return PlanOutput(
            error=(
                f"the model declined this goal: {_brief(str(e))}"
                if refused
                else f"the builder could not produce a valid plan: {_brief(str(e))}"
            ),
            terminal=refused,
        )
    path, tokens = _record(inp.tag, result)
    called = [
        p.tool_name
        for m in result.all_messages()
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolCallPart)
    ]
    return PlanOutput(
        plan=result.output, tools_called=called, trajectory=path, tokens=tokens
    )


def _plan_prompt(inp: PlanInput) -> str:
    """Everything the builder reasons from: goal, criteria, inputs, critique, history.

    The history is complete on reasoning and compact on data. A previous round's full
    outcome would be mostly sequence bases the builder cannot use and cannot afford.
    """
    parts = [
        f"Goal: {inp.goal}",
        f"Inputs (name: kind): {inp.input_kinds}",
    ]
    if inp.input_preview:
        parts.append(f"Input values: {json.dumps(inp.input_preview)}")
    if inp.criteria:
        shown = {c.id: c.claim for c in inp.criteria}
        parts.append(
            "Criteria you will be marked against (you cannot change these; every one "
            f"needs an assertion): {json.dumps(shown)}"
        )
    if inp.proposed:
        parts.append(f"The user's own idea, to take or leave: {inp.proposed}")
    if inp.brief:
        parts.append(f"What the literature says: {inp.brief.model_dump_json()}")
    if inp.critique:
        parts.append(
            "The last attempt was rejected. Honour its keep list and fix what it names:\n"
            + inp.critique.model_dump_json(indent=2)
        )
    if inp.history:
        parts.append(
            "Every attempt so far. Do not repeat a wiring you already ran:\n"
            + json.dumps([h.model_dump(mode="json") for h in inp.history], indent=2)
        )
    return "\n\n".join(parts)


def _registry_version() -> str:
    """A hash of this worker's node names.

    Unchanged across a resume means the worker was never restarted, so a node someone
    just wrote is not loaded here yet. That distinction is what lets the workflow say
    "restart the worker" instead of failing with the tool still missing.
    """
    return hashlib.sha256(",".join(sorted(NODES)).encode()).hexdigest()[:16]


@activity.defn
def resolve_plan(inp: ResolveInput) -> ResolveOutput:
    """Split a plan into what this worker can run and what it cannot.

    The only place the workflow reads the node registry. Returns the built Dag when
    nothing is missing, the contracts to ask a human for when something is, and the
    requested-versus-actual diff when a node now exists but with the wrong ports.

    Args:
        inp: The plan to resolve against this worker's registry.
        goal_inputs: The DAG input names the hypothesis actually has, with their kinds.
    """
    plan = inp.plan
    version = _registry_version()
    available = sorted(plan.node_names() & set(NODES))
    missing = [
        plan.requests[n]
        for n in sorted(plan.node_names() - set(NODES))
        if n in plan.requests
    ]
    unknown = sorted(plan.node_names() - set(NODES) - set(plan.requests))
    if unknown:
        return ResolveOutput(
            available=available,
            registry_version=version,
            error=f"steps use {unknown}, which neither exist nor have a ToolRequest",
        )

    # A node someone has now written, but not to the contract the plan was checked
    # against. The name resolves and the wiring then fails, so catch it here and say
    # exactly what differs rather than letting the run die at the moment of success.
    mismatched = {
        name: PortContract.of(NODES[name])
        for name, req in plan.requests.items()
        if name in NODES and PortContract.of(NODES[name]) != req.contract()
    }
    if missing or mismatched:
        return ResolveOutput(
            available=available,
            missing=missing,
            mismatched=mismatched,
            registry_version=version,
        )
    try:
        dag = Dag.model_validate(
            {
                "inputs": plan.inputs,
                "steps": {k: s.draft() for k, s in plan.steps.items()},
            }
        )
    except ValidationError as e:
        return ResolveOutput(
            available=available, registry_version=version, error=str(e)
        )
    # A Dag that declares inputs the hypothesis does not have cannot be run, and the
    # workflow would only find out when DagInput rejected it mid-round, as a pydantic
    # error about types rather than about the mistake. Say it here, where the critique
    # can act on it: the builder named an input that was never offered.
    if inp.goal_inputs and dag.inputs != inp.goal_inputs:
        return ResolveOutput(
            available=available,
            registry_version=version,
            error=(
                f"the plan declares inputs {dag.inputs}, but this hypothesis has "
                f"{inp.goal_inputs}. Wire the steps to the input names the goal "
                "actually offers."
            ),
        )
    return ResolveOutput(available=available, dag=dag, registry_version=version)


@activity.defn
async def verify_outcome(inp: VerifyInput) -> Verdict:
    """Run the verifier and return its opinion as an unaccepted Verdict.

    ``achieved`` is left false here on purpose. The workflow decides that from the
    criteria and the assertions; this activity only carries what the model thinks.

    Args:
        inp: What the verifier may see, and the model to judge with.
    """
    agent = verdict_agent(inp.model)
    try:
        result = await agent.run(inp.view.model_dump_json(indent=2))
    except UnexpectedModelBehavior as e:
        return Verdict(
            agrees=False,
            covers_goal=False,
            reason=f"the verifier failed: {_brief(str(e))}",
        )
    o = result.output
    _record(inp.tag, result)
    return Verdict(
        agrees=o.agrees,
        covers_goal=o.covers_goal,
        reason=o.reason,
        score=o.score,
        prediction_held=all(inp.view.held.values()) if inp.view.held else False,
    )


@activity.defn
async def critique_attempt(inp: CritiqueInput) -> CritiqueOutput:
    """Run the critic: why this attempt missed, and what the next plan must change.

    Args:
        inp: The attempt, the verdict, and the rounds already tried.
    """
    agent = critique_agent(inp.model)
    parts = [inp.view.model_dump_json(indent=2)]
    if inp.verdict:
        parts.append(f"The verifier said:\n{inp.verdict.model_dump_json(indent=2)}")
    if inp.history:
        parts.append(
            "Rounds already tried; do not send it back to one:\n"
            + json.dumps([h.model_dump(mode="json") for h in inp.history], indent=2)
        )
    try:
        result = await agent.run("\n\n".join(parts))
    except UnexpectedModelBehavior as e:
        return CritiqueOutput(error=_brief(str(e)), terminal=_refused(str(e)))
    path, tokens = _record(inp.tag, result)
    return CritiqueOutput(critique=result.output, trajectory=path, tokens=tokens)


@activity.defn
async def save_hypothesis_state(inp: SaveHypothesisInput) -> None:
    """Write the Hypothesis, so the pages can show how far the run has got.

    Args:
        inp: The Hypothesis to write.
    """
    save_hypothesis(inp.hypothesis)


@activity.defn
async def save_requests(inp: SaveRequestsInput) -> None:
    """Write a contract per requested node, so a human can pick the work up.

    The file holds the contract only. Who is blocked on it is computed by scanning the
    hypotheses, because two runs blocking on one tool would race a read-modify-write.

    Args:
        inp: The run that is blocked, and what it is blocked on.
    """
    for req in inp.requests:
        save_tool_request(req.name, req.model_dump_json(indent=2).encode())
