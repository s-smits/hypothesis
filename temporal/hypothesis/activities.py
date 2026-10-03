"""Everything non-deterministic: the model calls, and every read of the node registry.

An agent that cannot answer returns an ``error`` for the critique to work with; it never
crashes the run. Each returns its ``tokens``, so the workflow can account for every stage.
"""

import json
from typing import Any, cast

from pydantic import BaseModel, ValidationError
from pydantic_ai import RunUsage, capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_core import to_json
from temporalio import activity

from node_dag.agent import (
    NODES,
    Hypothesis,
    Seen,
    build_agent,
    cite,
    criteria_agent,
    critique_agent,
    plan_prompt,
    step_config,
    verify_agent,
)
from node_dag.dag import Dag
from node_dag.factory import NodeConfig
from node_dag.nodes.base import BaseFilterConfig
from node_dag.plan import (
    Criterion,
    Critique,
    Observation,
    Plan,
    ToolRequest,
    VerifyOpinion,
)
from node_dag.registry import Registry
from temporal.dag.activities import results_subdir, write_atomic


def save_hypothesis(hyp: Hypothesis) -> Hypothesis:
    """Write ``hyp`` to its file and return it."""
    write_atomic(
        results_subdir("hypotheses") / f"{hyp.id}.json",
        hyp.model_dump_json(indent=2).encode(),
    )
    return hyp


class Stage(BaseModel):
    """Input to an agent activity: the Hypothesis as it stands, and the model to use."""

    hyp: Hypothesis
    model: str


class Out(BaseModel):
    """What an agent activity returns: its answer, or an ``error``, and the tokens spent.

    ``observations`` are the records the plan cites, filled in from the literature the
    builder was shown.
    """

    criteria: list[Criterion] = []
    plan: Plan | None = None
    observations: list[Observation] = []
    opinion: VerifyOpinion | None = None
    critique: Critique | None = None
    error: str | None = None
    tokens: int = 0


class ResolveOut(BaseModel):
    """A plan resolved against this worker: a Dag, or the requested nodes still missing."""

    dag: Dag | None = None
    missing: list[ToolRequest] = []
    error: str | None = None


async def _ask(
    agent: Any,  # noqa: ANN401
    prompt: str,
    inp: Stage,
    stage: str,
    **kw: Any,  # noqa: ANN401
) -> dict[str, Any]:
    usage = RunUsage()  # Filled in as the run goes, so a call that fails still counts.
    with capture_run_messages() as messages:
        try:
            run = await agent.run(prompt, usage=usage, **kw)
        except UnexpectedModelBehavior as e:
            return {"error": str(e), "tokens": usage.total_tokens}
        finally:
            _record(f"{inp.hyp.id}-r{inp.hyp.round}-{stage}", messages)
    return {"out": run.output, "tokens": usage.total_tokens}


def _record(tag: str, messages: list[ModelMessage]) -> None:
    """Write a model call's messages, a failed call's too: they show which guard it hit."""
    try:
        write_atomic(
            results_subdir("trajectories") / f"{tag}.json",
            ModelMessagesTypeAdapter.dump_json(messages, indent=2),
        )
    except OSError as e:  # A transcript that cannot be written must not fail the run.
        activity.logger.warning("Could not write the %s transcript: %s", tag, e)


def _view(hyp: Hypothesis) -> dict[str, Any]:
    """The current round as a judge sees it. It has no earlier verdicts."""
    a = hyp.attempts[-1]
    assert a.plan
    return {
        "goal": hyp.goal,
        "criteria": hyp.criteria,
        "inputs": hyp.describe_inputs(),
        "hypothesis": a.plan.hypothesis,
        "expected": a.plan.expected,
        "assertions": a.plan.assertions,
        "held": a.held,
        "dag": a.dag,
        "outcome": a.outcome,
        "error": a.error,
    }


@activity.defn
async def derive_criteria(inp: Stage) -> Out:
    """Turn the goal into criteria. Runs once, before any plan, so the planner cannot choose them."""
    r = await _ask(
        criteria_agent(inp.model),
        f"Goal: {inp.hyp.goal}\nInputs: {json.dumps(inp.hyp.input_kinds())}",
        inp,
        "criteria",
    )
    return Out(
        criteria=r.get("out", []), error=r.get("error"), tokens=r.get("tokens", 0)
    )


@activity.defn
async def plan_hypothesis(inp: Stage) -> Out:
    """Run the builder, which sees every earlier attempt, and return its Plan."""
    seen: Seen = {}
    agent = build_agent(inp.model, Registry(results_subdir("registry")), seen)
    r = await _ask(agent, plan_prompt(inp.hyp), inp, "plan", deps=inp.hyp)
    plan: Plan | None = r.get("out")
    return Out(
        plan=plan,
        observations=cite(plan, seen) if plan else [],
        error=r.get("error") and f"no valid plan: {r['error']}",
        tokens=r.get("tokens", 0),
    )


@activity.defn
async def verify_outcome(inp: Stage) -> Out:
    """Run the verifier on the current round alone. It can veto; it cannot accept."""
    r = await _ask(
        verify_agent(inp.model), to_json(_view(inp.hyp)).decode(), inp, "verify"
    )
    return Out(opinion=r.get("out"), error=r.get("error"), tokens=r.get("tokens", 0))


@activity.defn
async def critique_attempt(inp: Stage) -> Out:
    """Run the critic on the current round, with a summary of the earlier ones."""
    earlier = [a.summary() for a in inp.hyp.attempts[:-1]]
    verdict = inp.hyp.attempts[-1].verdict
    r = await _ask(
        critique_agent(inp.model),
        to_json({**_view(inp.hyp), "verdict": verdict, "earlier": earlier}).decode(),
        inp,
        "critique",
    )
    return Out(critique=r.get("out"), error=r.get("error"), tokens=r.get("tokens", 0))


@activity.defn
def resolve_plan(plan: Plan) -> ResolveOut:
    """Build a plan's Dag against this worker, or name the requested nodes it lacks.

    The only read of the node registry: its answer changes whenever a node is added and
    the worker restarted, so it must be recorded in history, not recomputed on replay.
    """
    if gone := [plan.requests[n] for n in sorted(plan.requests.keys() - NODES.keys())]:
        return ResolveOut(missing=gone)
    registry = Registry(results_subdir("registry"))
    try:
        configs = {k: step_config(plan, k, registry) for k in plan.steps}
        for k in sorted(
            configs, key=lambda k: isinstance(configs[k], BaseFilterConfig)
        ):
            # Scorers first: a filter needs its column. Every requested node exists by now,
            # so no stand-in is left among the configs.
            registry.register(cast("NodeConfig", configs[k]), plan.steps[k].why)
        steps = {
            k: {"config": configs[k].model_dump(mode="json"), "inputs": s.inputs}
            for k, s in plan.steps.items()
        }
        return ResolveOut(
            dag=Dag.model_validate({"inputs": plan.inputs, "steps": steps})
        )
    except (ValidationError, ValueError, TypeError) as e:
        # A requested node that now exists with other fields or ports: stay blocked, saying how.
        return ResolveOut(missing=list(plan.requests.values()), error=str(e))


@activity.defn
def save_state(hyp: Hypothesis) -> None:
    """Write the Hypothesis, so the pages can follow the run."""
    save_hypothesis(hyp)


@activity.defn
def save_requests(requests: list[ToolRequest]) -> None:
    """Write each contract, so a person can write the node without asking a question."""
    for r in requests:
        write_atomic(
            results_subdir("requests") / f"{r.name}.json",
            r.model_dump_json(indent=2).encode(),
        )
