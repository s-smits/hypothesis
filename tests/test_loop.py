import asyncio
from concurrent.futures import ThreadPoolExecutor

from pydantic_core import to_json
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from test_guards import ASK, COLUMN, _plan
from test_guards import HYP as GUARD_HYP

from node_dag.agent import Hypothesis, plan_prompt
from node_dag.dag import Dag
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.plan import Critique, Plan, ToolRequest, VerifyOpinion
from node_dag.types import Dna
from temporal.dag.activities import run_filter, run_score, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.activities import (
    Out,
    ResolveOut,
    Stage,
    _view,
    save_requests,
    save_state,
)
from temporal.hypothesis.loop import HypothesisInput, HypothesisLoop

HYP = GUARD_HYP.model_copy(
    update={"inputs": {"seq": [Dna(sequence="ATGTCTTAA")]}}
)  # No TCG, so the claim holds.

PLAN = Plan.model_validate(_plan())
COUNT = CodonCountConfig(codons=("TCG",))
STEPS = {
    "counted": {"config": COUNT.model_dump(mode="json"), "inputs": {"sequence": "seq"}},
    "small": {
        "config": AtMostConfig(column=COLUMN, threshold=0).model_dump(mode="json"),
        "inputs": {"items": "counted"},
    },
}
DAG = Dag.model_validate({"inputs": {"seq": "dna"}, "steps": STEPS})
RAISES = Dag.model_validate(
    {
        "inputs": {"seq": "dna"},
        "steps": {
            "recode": {
                "config": RecodeCodonsConfig(targets=("ATG",)).model_dump(mode="json"),
                "inputs": {"sequence": "seq"},
            }
        },
    }
)
REQUEST = ToolRequest(name="gc_count", node="score", output=["gc"], **ASK)
CRITIQUE = Critique(
    diagnosis="d",
    root_cause="wrong_config",
    evidence=["e"],
    fix="try another threshold",
)


def _fakes(calls: dict, plans: list, resolves: list, agrees: list[bool]) -> list:
    @activity.defn(name="plan_hypothesis")
    async def plan(inp: Stage) -> Out:
        calls.setdefault("plan", []).append(inp)
        return Out(plan=plans.pop(0), tokens=10)

    @activity.defn(name="resolve_plan")
    def resolve(plan: Plan) -> ResolveOut:
        calls.setdefault("resolve", []).append(plan)
        return resolves.pop(0)

    @activity.defn(name="verify_outcome")
    async def verify(inp: Stage) -> Out:
        calls.setdefault("verify", []).append(inp)
        return Out(
            opinion=VerifyOpinion(
                agrees=agrees.pop(0), covers_goal=True, reason="the opinion"
            ),
            tokens=5,
        )

    @activity.defn(name="critique_attempt")
    async def critique(inp: Stage) -> Out:
        return Out(critique=CRITIQUE, tokens=3)

    return [plan, resolve, verify, critique]


async def _drive(fakes: list, hyp: Hypothesis = HYP, steer=None, **cfg) -> Hypothesis:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        with ThreadPoolExecutor() as pool:
            acts = [
                run_tool,
                run_score,
                run_filter,
                save_workflow,
                save_state,
                save_requests,
                *fakes,
            ]
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[HypothesisLoop, DagWorkflow],
                activities=acts,
                activity_executor=pool,
            ):
                inp = HypothesisInput(
                    hypothesis=hyp, build_model="b", verify_model="v", **cfg
                )
                handle = await env.client.start_workflow(
                    HypothesisLoop.run, inp, id=hyp.id, task_queue=TASK_QUEUE
                )
                if steer:
                    while (await handle.query(HypothesisLoop.state)).state != "blocked":
                        await asyncio.sleep(0.05)
                    await steer(handle)
                return await handle.result()


async def test_a_plan_whose_nodes_exist_is_achieved_in_one_round_and_every_stage_is_counted():
    calls: dict = {}
    done = await _drive(_fakes(calls, [PLAN], [ResolveOut(dag=DAG)], [True]))
    cur = done.current
    assert done.state == "achieved" and done.round == 1
    assert cur and cur.verdict and cur.verdict.achieved and cur.outcome and cur.dag
    assert cur.workflow_id == f"{HYP.id}-r1"
    assert done.usage == {"plan": 10, "verify": 5, "total": 15}
    assert done.attempts[0].held == {"small.yes": True}
    # The round is stored once, under attempts, not copied to the top.
    assert (
        not {"dag", "outcome", "verdict", "workflow_id", "pending"}
        & done.model_dump().keys()
    )


def test_the_users_own_idea_reaches_the_builder():
    idea = HYP.model_copy(update={"hypothesis": "swap TCG for TCC"})
    assert "swap TCG for TCC" in plan_prompt(idea)
    assert "own idea" not in plan_prompt(HYP)


async def test_a_missing_tool_blocks_until_it_is_added_then_resolves_the_same_plan(
    results_dir,
):
    calls: dict = {}
    seen: list[Hypothesis] = []

    async def added(handle) -> None:
        seen.append(await handle.query(HypothesisLoop.state))
        await handle.signal(HypothesisLoop.tool_added)

    fakes = _fakes(
        calls, [PLAN], [ResolveOut(missing=[REQUEST]), ResolveOut(dag=DAG)], [True]
    )
    done = await _drive(fakes, steer=added)
    assert (
        seen[0].state == "blocked"
        and seen[0].pending == [REQUEST]
        and seen[0].attempts[0].plan
    )  # The plan shows while blocked.
    assert (results_dir / "requests" / "gc_count.json").exists()
    assert done.state == "achieved" and (len(calls["plan"]), len(calls["resolve"])) == (
        1,
        2,
    )


async def test_abandoning_a_blocked_run_ends_it():
    abandon = lambda h: h.signal(HypothesisLoop.abandon)
    done = await _drive(
        _fakes({}, [PLAN], [ResolveOut(missing=[REQUEST])], []), steer=abandon
    )
    assert done.state == "abandoned" and done.pending == []


async def test_a_rejected_round_is_critiqued_and_the_verifier_never_sees_earlier_verdicts():
    calls: dict = {}
    other = Plan.model_validate(
        _plan(addresses_critique="allows one", hypothesis="second")
    )
    done = await _drive(
        _fakes(calls, [PLAN, other], [ResolveOut(dag=DAG)] * 2, [False, True])
    )
    assert (
        done.state == "achieved"
        and done.round == 2
        and done.attempts[0].critique == CRITIQUE
    )
    assert "try another threshold" in plan_prompt(calls["plan"][1].hyp)
    assert "the opinion" not in to_json(_view(calls["verify"][1].hyp)).decode()
    assert (
        done.attempts[0].outcome is None and done.attempts[0].produced
    )  # Older rounds keep previews, not outcomes.


async def test_a_node_that_raises_becomes_a_critique_not_a_crash():
    done = await _drive(_fakes({}, [PLAN], [ResolveOut(dag=RAISES)], []), max_rounds=1)
    (att,) = done.attempts
    assert (
        done.state == "not achieved"
        and "Every synonym" in (att.error or "")
        and att.critique == CRITIQUE
    )


async def test_an_agent_activity_that_keeps_failing_ends_the_run_as_failed(results_dir):
    fakes = _fakes({}, [], [], [])

    @activity.defn(name="plan_hypothesis")
    async def down(inp: Stage) -> Out:
        raise RuntimeError("529 overloaded")

    done = await _drive([down, *fakes[1:]])
    assert done.state == "failed" and "plan_hypothesis failed" in (
        done.stopped_because or ""
    )
    assert "529 overloaded" in (done.stopped_because or "")
    saved = Hypothesis.model_validate_json(
        (results_dir / "hypotheses" / f"{HYP.id}.json").read_bytes()
    )
    assert saved.state == "failed"  # The pages show it ended, not "building".


async def test_a_resume_sent_while_the_plan_resolves_again_is_not_lost():
    resolves = [
        ResolveOut(missing=[REQUEST]),
        ResolveOut(missing=[REQUEST]),
        ResolveOut(dag=DAG),
    ]
    fakes = _fakes({}, [PLAN], [], [True])

    @activity.defn(name="resolve_plan")
    async def resolve(plan: Plan) -> ResolveOut:
        if len(resolves) == 2:  # The second resolve: resume again before it answers.
            await (
                activity.client()
                .get_workflow_handle(HYP.id)
                .signal(HypothesisLoop.tool_added)
            )
        return resolves.pop(0)

    resume = lambda h: h.signal(HypothesisLoop.tool_added)
    done = await asyncio.wait_for(
        _drive([fakes[0], resolve, *fakes[2:]], steer=resume), 30
    )
    assert done.state == "achieved" and not resolves
