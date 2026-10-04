"""Everything non-deterministic: the model calls, and every read of the node registry.

An agent that cannot answer returns an ``error`` for the critique to work with; it never
crashes the run. Each returns its ``tokens``, so the workflow can account for every stage.
"""

import asyncio
import inspect
import json
import math
from typing import Any, cast

import httpx2
from pydantic import BaseModel, ValidationError
from pydantic_ai import RunUsage, capture_run_messages
from pydantic_ai.exceptions import ContentFilterError, UnexpectedModelBehavior
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
    inputs_agent,
    plan_prompt,
    step_config,
    verify_agent,
)
from node_dag.dag import Dag, DagOutput
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
from node_dag.types import Table, Value
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

    ``inputs`` and their ``sources`` are what the inputs agent found.

    ``declined`` is set when the model refused the request: asking again would only be
    refused again.

    ``observations`` are the hypothesis's records after this plan: those the user kept,
    the ones the plan cites, filled in from the literature the builder was shown.
    """

    inputs: dict[str, list[Value]] = {}
    sources: dict[str, str] = {}
    criteria: list[Criterion] = []
    plan: Plan | None = None
    observations: list[Observation] = []
    opinion: VerifyOpinion | None = None
    critique: Critique | None = None
    error: str | None = None
    declined: bool = False
    tokens: int = 0


class ResolveOut(BaseModel):
    """A plan resolved against this worker: a Dag, or the requested nodes still missing."""

    dag: Dag | None = None
    missing: list[ToolRequest] = []
    error: str | None = None


# Seconds of silence from the API before a request is given up and retried by the client.
# A response streams, so a live request is never quiet for long; a dead one is quiet for
# ever. Unset, the client waits 600 s, the same as the activity's start_to_close, so the
# client's retries never run and a whole attempt is lost (arm F2 lost 10 minutes twice).
# A bare number would also replace the client's 5 s connect limit, so it is kept.
REQUEST_TIMEOUT = 90.0
CONNECT_TIMEOUT = 5.0


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
            timeout = httpx2.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
            run = await agent.run(
                prompt, usage=usage, model_settings={"timeout": timeout}, **kw
            )
        except ContentFilterError as e:
            return {"error": e.message, "declined": True, "tokens": usage.total_tokens}
        except UnexpectedModelBehavior as e:
            return {"error": e.message, "tokens": usage.total_tokens}
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


def _what_the_nodes_do(dag: Dag | None) -> dict[str, str]:
    """What each node in the DAG says it does, from its own documentation before ``Args:``."""
    names = sorted({s.config.name for s in dag.steps.values()}) if dag else []
    return {
        n: inspect.cleandoc(NODES[n].__doc__ or "").split("\n\nArgs:")[0] for n in names
    }


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
        "nodes": _what_the_nodes_do(a.dag),
        "dag": a.dag,
        "outcome": a.outcome,
        "error": a.error,
    }


# What one judge call may carry, in characters. A call is planned at about 100k tokens,
# half the 200k window, because opencode, hermes-agent and prime-agent all compact at
# 75-84% and none of them plans a call to the edge. The 673k-character prompt that failed
# at 388,463 tokens measured 1.73 characters per token on DNA; 1.5 keeps a margin.
PROMPT_CHARS = 150_000
# A sequence this long or shorter is shown whole; a longer one shows its two ends.
SHOWN = 200
# Most calls one judging may make. Past what they hold, every n-th entity is shown.
MAX_SHARDS = 3


def _ends(sequence: str) -> str:
    if len(sequence) <= SHOWN:
        return sequence
    return (
        f"{sequence[: SHOWN // 2]}...{sequence[-SHOWN // 2 :]} ({len(sequence)} long)"
    )


def _ranges(table: Table) -> dict[str, dict[str, float]]:
    """Each score column's size, lowest, highest and mean over the whole table."""
    return {
        c: {"n": len(v), "min": min(v.values()), "max": max(v.values()), "mean": round(sum(v.values()) / len(v), 4)}
        for c, v in table.scores.items()
        if v
    }  # fmt: skip


def _compact(outcome: DagOutput, only: set[str] | None = None) -> dict[str, Any]:
    """The outcome with each entity once, however many tables hold it.

    Each table keeps its ids and their scores, and its whole size and score ranges, so a
    share of the entities (``only``) still shows the totals a judge needs.
    """
    entities = {i.id: i for t in outcome.values.values() for i in t.items}
    keep = [i for i in entities if only is None or i in only]
    tables = {}
    for k, t in outcome.values.items():
        ids = [i.id for i in t.items if only is None or i.id in only]
        scores = {c: {i: v[i] for i in ids if i in v} for c, v in t.scores.items()}
        tables[k] = {
            "count": len(t.items),
            "ranges": _ranges(t),
            "ids": ids,
            "scores": scores,
        }
    return {
        "about": "Each entity is listed once under entities, with its kind and sequence "
        "(a long one shows its two ends and its length). tables gives, for each source, how "
        "many entities it holds, the range of each score column over all of them, and the "
        "ids and scores of those listed here.",
        "entities": {i: {"kind": entities[i].kind, "sequence": _ends(entities[i].sequence)} for i in keep},
        "tables": tables,
        "skipped": outcome.skipped,
    }  # fmt: skip


def _views(hyp: Hypothesis) -> list[dict[str, Any]]:
    """What the judges are sent: the whole view when it fits, else less of it, in calls.

    A view that fits is sent as it is. One that does not lists each entity once and shows
    long sequences by their ends. If that is still too large, the entities are shared out
    over calls (at most ``MAX_SHARDS``), each carrying all else, so every call sees the
    plan, the assertions and every table's totals, and a ``chunk`` that says which share it
    holds. Past what the calls hold, every n-th entity is shown, and ``chunk`` says so.
    """
    whole = _view(hyp)
    outcome = hyp.attempts[-1].outcome
    if outcome is None or len(to_json(whole)) <= PROMPT_CHARS:
        return [whole]
    lean = {
        **whole,
        "inputs": {
            k: {**v, "sequences": [_ends(s) for s in v["sequences"]]}
            for k, v in whole["inputs"].items()
        },
    }
    one = {**lean, "outcome": _compact(outcome)}
    if len(to_json(one)) <= PROMPT_CHARS:
        return [one]
    # What an entity adds to a call: itself, and its id and scores in each table.
    tables = outcome.values.values()
    cost = {}
    for t in tables:
        for i in t.items:
            row = cost.get(i.id) or len(
                to_json({i.id: {"kind": i.kind, "sequence": _ends(i.sequence)}})
            )
            cost[i.id] = row + len(i.id) + 6 + sum(len(i.id) + len(str(v[i.id])) + 6 for v in t.scores.values() if i.id in v)  # fmt: skip
    ids = list(cost)
    room = PROMPT_CHARS - len(to_json({**lean, "outcome": _compact(outcome, set())}))
    while True:
        step = max(1, math.ceil(sum(cost.values()) / (MAX_SHARDS * max(room, 1))))
        shown = ids[::step]
        shards: list[list[str]] = [[]]
        used = 0
        for i in shown:
            if shards[-1] and used + cost[i] > room:
                shards.append([])
                used = 0
            shards[-1].append(i)
            used += cost[i]
        views = [
            {
                **lean,
                "chunk": {"share": n, "of": len(shards), "entities": len(ids), "listed": len(shown), "every": step},
                "outcome": _compact(outcome, set(share)),
            }
            for n, share in enumerate(shards, 1)
        ]  # fmt: skip
        if len(shards) <= MAX_SHARDS and all(
            len(to_json(v)) <= PROMPT_CHARS for v in views
        ):
            return views
        if room < 1000:  # Nothing left to share out: send what is smallest.
            return views[:MAX_SHARDS]
        room = int(room * 0.8)  # The cost was an estimate, and it was too low.


@activity.defn
async def draft_inputs(inp: Stage) -> Out:
    """Find the sequences a goal with no inputs is about. Runs once, before round 1."""
    agent, found = inputs_agent(inp.model)
    hyp = inp.hyp
    prompt = f"Goal: {hyp.goal}" + (
        f"\nProposed hypothesis: {hyp.hypothesis}" if hyp.hypothesis else ""
    )
    r = await _ask(agent, prompt, inp, "inputs")
    return Out(
        inputs=found.inputs,
        sources=found.sources,
        error=r.get("error"),
        declined=r.get("declined", False),
        tokens=r.get("tokens", 0),
    )


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
        criteria=[c.model_copy(update={"source": "derived"}) for c in r.get("out", [])],
        error=r.get("error"),
        declined=r.get("declined", False),
        tokens=r.get("tokens", 0),
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
        observations=cite(plan, seen, inp.hyp.observations) if plan else [],
        error=r.get("error") and f"no valid plan: {r['error']}",
        declined=r.get("declined", False),
        tokens=r.get("tokens", 0),
    )


@activity.defn
async def verify_outcome(inp: Stage) -> Out:
    """Run the verifier on the current round alone. It can veto; it cannot accept.

    An outcome too large for one call is shared over several, and the round is vetoed if
    any of them vetoes: a veto needs evidence in what that call saw, and no call can
    wave through what another saw.
    """
    views = _views(inp.hyp)
    asked = await asyncio.gather(
        *(
            _ask(
                verify_agent(inp.model),
                to_json(v).decode(),
                inp,
                "verify" if len(views) == 1 else f"verify-{n}of{len(views)}",
            )
            for n, v in enumerate(views, 1)
        )
    )
    tokens = sum(r.get("tokens", 0) for r in asked)
    if failed := next((r for r in asked if "error" in r), None):
        return Out(
            error=failed["error"], declined=failed.get("declined", False), tokens=tokens
        )
    opinions: list[VerifyOpinion] = [r["out"] for r in asked]
    if len(opinions) == 1:
        return Out(opinion=opinions[0], tokens=tokens)
    said = [
        (n, o) for n, o in enumerate(opinions, 1) if not (o.agrees and o.covers_goal)
    ]
    return Out(
        opinion=VerifyOpinion(
            agrees=all(o.agrees for o in opinions),
            covers_goal=all(o.covers_goal for o in opinions),
            reason="; ".join(f"[share {n} of {len(opinions)}] {o.reason}" for n, o in said or [(1, opinions[0])]),
        ),
        tokens=tokens,
    )  # fmt: skip


@activity.defn
async def critique_attempt(inp: Stage) -> Out:
    """Run the critic on the current round, with a summary of the earlier ones."""
    earlier = [a.summary() for a in inp.hyp.attempts[:-1]]
    verdict = inp.hyp.attempts[-1].verdict
    r = await _ask(
        critique_agent(inp.model),
        to_json(
            {**_views(inp.hyp)[0], "verdict": verdict, "earlier": earlier}
        ).decode(),
        inp,
        "critique",
    )
    return Out(
        critique=r.get("out"),
        error=r.get("error"),
        declined=r.get("declined", False),
        tokens=r.get("tokens", 0),
    )


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
