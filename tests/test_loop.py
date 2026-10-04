import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic_core import to_json
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from test_guards import ASK, COLUMN, _plan, _step
from test_guards import HYP as GUARD_HYP

from node_dag.agent import Hypothesis, plan_prompt
from node_dag.dag import Dag
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.esmfold2_fold.config import Esmfold2FoldConfig
from node_dag.plan import (
    Attempt,
    Criterion,
    Critique,
    Observation,
    Plan,
    ToolRequest,
    VerifyOpinion,
)
from node_dag.types import Dna
from temporal.dag.activities import run_filter, run_score, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.activities import (
    Out,
    ResolveOut,
    Stage,
    _view,
    resolve_plan,
    save_requests,
    save_state,
)
from temporal.hypothesis.loop import HypothesisInput, HypothesisLoop
from temporal.ledger import ledger_path, read, record_ledger

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
            "protein": {
                "config": DnaToProteinConfig().model_dump(mode="json"),
                "inputs": {"sequence": "seq"},
            },
            "fold": {
                "config": Esmfold2FoldConfig().model_dump(mode="json"),
                "inputs": {"sequence": "protein"},
            },
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


def _fakes(
    calls: dict,
    plans: list,
    resolves: list,
    agrees: list[bool],
    cited: list[list[Observation]] | None = None,
    covers: bool = True,
) -> list:
    cited = list(cited or [])

    @activity.defn(name="plan_hypothesis")
    async def plan(inp: Stage) -> Out:
        calls.setdefault("plan", []).append(inp)
        # The records each plan cites, round by round, as the real activity returns them.
        return Out(
            plan=plans.pop(0), observations=cited.pop(0) if cited else [], tokens=10
        )

    @activity.defn(name="resolve_plan")
    def resolve(plan: Plan) -> ResolveOut:
        calls.setdefault("resolve", []).append(plan)
        return resolves.pop(0)

    @activity.defn(name="verify_outcome")
    async def verify(inp: Stage) -> Out:
        calls.setdefault("verify", []).append(inp)
        return Out(
            opinion=VerifyOpinion(
                agrees=agrees.pop(0), covers_goal=covers, reason="the opinion"
            ),
            tokens=5,
        )

    @activity.defn(name="critique_attempt")
    async def critique(inp: Stage) -> Out:
        return Out(critique=CRITIQUE, tokens=3)

    return [plan, resolve, verify, critique]


async def _drive(
    fakes: list,
    hyp: Hypothesis = HYP,
    steer=None,
    ledger=record_ledger,
    cancel_on: asyncio.Event | None = None,
    **cfg,
) -> Hypothesis:
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
                ledger,
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
                if cancel_on:  # Cancel the workflow once this is set.
                    await asyncio.wait_for(cancel_on.wait(), 10)
                    await handle.cancel()
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


def test_the_judges_are_told_what_each_node_in_the_dag_does():
    judged = HYP.model_copy(update={"attempts": [Attempt(round=1, plan=PLAN, dag=DAG)]})
    nodes = _view(judged)["nodes"]
    assert set(nodes) == {"codon_count", "at_most"}
    assert "Args:" not in nodes["codon_count"] and nodes["at_most"]
    assert (
        _view(HYP.model_copy(update={"attempts": [Attempt(round=1, plan=PLAN)]}))[
            "nodes"
        ]
        == {}
    )


def test_the_criteria_reach_the_builder_and_the_verifier():
    assert "no TCG remains" in plan_prompt(HYP)
    judged = HYP.model_copy(update={"attempts": [Attempt(round=1, plan=PLAN)]})
    assert "no TCG remains" in to_json(_view(judged)).decode()


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


async def test_the_observations_on_the_hypothesis_are_the_current_rounds_plans(
    results_dir,
):
    def found(n: int) -> list[Observation]:
        return [
            Observation(
                amass_id=f"AMBC_{n}", summary=f"s{n}", core="biomedcore", title=f"t{n}"
            )
        ]

    other = Plan.model_validate(
        _plan(addresses_critique="allows one", hypothesis="second")
    )
    fakes = _fakes(
        {},
        [PLAN, other],
        [ResolveOut(dag=DAG)] * 2,
        [False, True],
        [found(1), found(2)],
    )
    done = await _drive(fakes)
    assert done.round == 2 and done.observations == found(2)
    saved = Hypothesis.model_validate_json(
        (results_dir / "hypotheses" / f"{HYP.id}.json").read_bytes()
    )
    assert saved.observations == found(2)  # What the pages read.


async def test_a_node_that_raises_becomes_a_critique_not_a_crash():
    stops = HYP.model_copy(update={"inputs": {"seq": [Dna(sequence="ATGTAAGCT")]}})
    fakes = _fakes({}, [PLAN], [ResolveOut(dag=RAISES)], [])
    done = await _drive(fakes, hyp=stops, max_rounds=1)
    (att,) = done.attempts
    assert (
        done.state == "not achieved"
        and "stop codon mid-sequence" in (att.error or "")
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


async def test_a_bug_in_the_loop_ends_the_run_as_failed_instead_of_wedging_it(
    results_dir,
):
    # A Dag whose input the hypothesis does not have: DagInput refuses it inside the loop.
    stray = Dag.model_validate(
        {
            "inputs": {"other": "dna"},
            "steps": {
                "counted": {
                    "config": COUNT.model_dump(mode="json"),
                    "inputs": {"sequence": "other"},
                }
            },
        }
    )
    done = await _drive(_fakes({}, [PLAN], [ResolveOut(dag=stray)], []))
    assert done.state == "failed" and "the loop stopped" in (done.stopped_because or "")
    saved = Hypothesis.model_validate_json(
        (results_dir / "hypotheses" / f"{HYP.id}.json").read_bytes()
    )
    assert saved.state == "failed"


async def test_a_goal_with_no_inputs_has_them_fetched_once_and_frozen_before_round_1(
    results_dir,
):
    found = {"seq": [Dna(sequence="ATGTCTTAA")]}
    asked = []

    @activity.defn(name="draft_inputs")
    async def draft(inp: Stage) -> Out:
        asked.append(inp.hyp.inputs)
        return Out(inputs=found, sources={"seq": "NCBI J01636.1 CDS lacZ"}, tokens=9)

    calls: dict = {}
    fakes = _fakes(calls, [PLAN], [ResolveOut(dag=DAG)], [True])
    bare = HYP.model_copy(update={"inputs": {}})
    done = await _drive([draft, *fakes], hyp=bare)
    assert asked == [{}] and done.state == "achieved"
    assert done.inputs == found and done.input_sources == {
        "seq": "NCBI J01636.1 CDS lacZ"
    }
    assert calls["plan"][0].hyp.inputs == found  # The builder is shown them.
    assert done.usage["inputs"] == 9


async def test_a_goal_whose_inputs_cannot_be_found_ends_the_run_as_failed():
    @activity.defn(name="draft_inputs")
    async def none_found(inp: Stage) -> Out:
        return Out(error="no record for that gene", tokens=4)

    bare = HYP.model_copy(update={"inputs": {}})
    done = await _drive([none_found, *_fakes({}, [], [], [])], hyp=bare)
    assert done.state == "failed"
    assert "no inputs could be found" in (done.stopped_because or "")


async def test_a_model_that_declines_ends_the_run_instead_of_being_asked_again():
    asked = []
    fakes = _fakes({}, [], [], [])

    @activity.defn(name="plan_hypothesis")
    async def declines(inp: Stage) -> Out:
        asked.append(inp.hyp.round)
        return Out(error="refused", declined=True, tokens=7)

    done = await _drive([declines, *fakes[1:]])
    assert asked == [1]  # Not again in round 2 and 3.
    assert done.state == "failed" and "declined the plan request" in (
        done.stopped_because or ""
    )
    assert done.usage["plan"] == 7  # What the refused call cost is still counted.


def test_the_models_default_to_two_different_ones():
    inp = HypothesisInput(hypothesis=HYP)
    assert inp.build_model != inp.verify_model


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


async def test_every_finished_run_leaves_a_line_in_the_ledger_whatever_its_end():
    achieved = await _drive(_fakes({}, [PLAN], [ResolveOut(dag=DAG)], [True]))
    abandon = lambda h: h.signal(HypothesisLoop.abandon)
    asking = Plan.model_validate(_plan(requests={"gc_count": REQUEST.model_dump()}))
    await _drive(
        _fakes({}, [asking], [ResolveOut(missing=[REQUEST])], []), steer=abandon
    )
    first, second = read(ledger_path())[0]
    assert (first.state, first.rounds, first.held) == ("achieved", 1, ["1/1"])
    assert first.hypothesis == achieved.id and first.tokens == 15
    assert (second.state, second.asked) == ("abandoned", ["gc_count"])
    assert first.run != second.run


async def test_a_ledger_that_cannot_be_written_does_not_change_how_the_run_ended():
    @activity.defn(name="record_ledger")
    def broken(hyp_id: str) -> None:
        raise ApplicationError("disk full", non_retryable=True)

    done = await _drive(
        _fakes({}, [PLAN], [ResolveOut(dag=DAG)], [True]), ledger=broken
    )
    assert done.state == "achieved" and not ledger_path().exists()


async def test_a_cancel_during_the_ledger_write_still_cancels_the_run():
    writing = asyncio.Event()

    @activity.defn(name="record_ledger")
    async def stuck(hyp_id: str) -> None:
        writing.set()
        await asyncio.Event().wait()

    with pytest.raises(WorkflowFailureError) as raised:
        await asyncio.wait_for(
            _drive(
                _fakes({}, [PLAN], [ResolveOut(dag=DAG)], [True]),
                ledger=stuck,
                cancel_on=writing,
            ),
            30,
        )
    assert isinstance(raised.value.cause, CancelledError)


# The accept path against plans that look right. The verifier here is scripted: acceptance
# is code, so each case says what the code must do whatever the verifier says.
def _none_achieved(done: Hypothesis) -> bool:
    return not any(a.verdict and a.verdict.achieved for a in done.attempts)


@pytest.mark.parametrize(("agrees", "covers"), [(False, True), (True, False)])
async def test_assertions_that_all_hold_do_not_achieve_a_goal_the_verifier_vetoes(
    agrees, covers
):
    fakes = _fakes(
        {}, [PLAN] * 3, [ResolveOut(dag=DAG)] * 3, [agrees] * 3, covers=covers
    )
    done = await _drive(fakes)
    assert done.state == "not achieved" and len(done.attempts) == 3
    assert all(a.held == {"small.yes": True} for a in done.attempts)
    assert _none_achieved(done)  # The code was content; the verifier stopped it.


# Nothing is filtered out, so "the filter produced something" holds whatever the outcome.
LENIENT = {
    **STEPS,
    "small": {
        "config": AtMostConfig(column=COLUMN, threshold=100).model_dump(mode="json"),
        "inputs": {"items": "counted"},
    },
}
VACUOUS = Plan.model_validate(
    _plan(
        steps={
            "counted": _step("codon_count", "seq", codons=["TCG"]),
            "small": _step("at_most", "counted", "items", column=COLUMN, threshold=100),
        },
        assertions=[
            {"criterion": "no_tcg", "step": "small", "branch": "produced", "claim": "c"}
        ],
    )
)


async def test_a_vacuous_assertion_alone_does_not_achieve_a_goal_the_verifier_vetoes():
    dag = Dag.model_validate({"inputs": {"seq": "dna"}, "steps": LENIENT})
    fakes = _fakes({}, [VACUOUS] * 2, [ResolveOut(dag=dag)] * 2, [False] * 2)
    done = await _drive(fakes, hyp=GUARD_HYP, max_rounds=2)  # ATGTCGTAA keeps its TCG.
    first = done.attempts[0]
    assert first.held == {"small.produced": True}  # The code cannot tell it is vacuous.
    assert len(first.produced["small.yes"]) == 1 and first.produced["small.no"] == []
    assert done.state == "not achieved" and _none_achieved(done)


async def test_a_criterion_no_assertion_covers_is_not_achieved_when_the_verifier_agrees():
    unmet = Criterion(id="unmet", claim="a thing no node measures")
    hyp = HYP.model_copy(update={"criteria": [*HYP.criteria, unmet]})
    fakes = _fakes({}, [PLAN] * 2, [ResolveOut(dag=DAG)] * 2, [True] * 2)
    done = await _drive(fakes, hyp=hyp, max_rounds=2)
    assert done.state == "not achieved" and _none_achieved(done)
    for a in done.attempts:
        assert a.verdict and "no assertion covers ['unmet']" in a.verdict.reason
        assert a.verdict.agrees and a.held == {"small.yes": True}


async def test_a_verifier_that_agrees_cannot_achieve_an_assertion_that_did_not_hold():
    fakes = _fakes({}, [PLAN] * 2, [ResolveOut(dag=DAG)] * 2, [True] * 2)
    done = await _drive(fakes, hyp=GUARD_HYP, max_rounds=2)  # TCG: nothing is kept.
    assert done.state == "not achieved" and _none_achieved(done)
    for a in done.attempts:
        assert a.held == {"small.yes": False}
        assert a.verdict and a.verdict.agrees and a.verdict.covers_goal
        assert "did not hold" in a.verdict.reason


# The first sequence has one TCG, the others none and two: keep what has fewer than it.
FIRST = Dna(sequence="ATGTCGTAA")
FEWER = HYP.model_copy(
    update={
        "inputs": {
            "seq": [FIRST, Dna(sequence="ATGTCTTAA"), Dna(sequence="ATGTCGTCGTAA")]
        },
        "criteria": [
            Criterion(id="fewer", claim="kept ones have fewer TCG than the first")
        ],
    }
)


def _beats(reference: Dna) -> Plan:
    steps = {
        "counted": _step("codon_count", "seq", codons=["TCG"]),
        "better": _step(
            "beats_reference",
            "counted",
            "items",
            column=COLUMN,
            reference=reference.model_dump(mode="json"),
            scored_in="counted",
            higher=False,
        ),
    }
    ask = [{"criterion": "fewer", "step": "better", "branch": "produced", "claim": "c"}]
    return Plan.model_validate(_plan(steps=steps, assertions=ask))


async def _drive_beats(reference: Dna, agrees: list[bool], calls: dict, **cfg):
    real = _fakes(calls, [_beats(reference)] * 2, [], agrees)
    fakes = [real[0], resolve_plan, *real[2:]]  # The plan is resolved by the real one.
    return await asyncio.wait_for(_drive(fakes, hyp=FEWER, **cfg), 60)


@pytest.mark.parametrize(
    ("agrees", "state"), [(True, "achieved"), (False, "not achieved")]
)
async def test_a_beats_reference_plan_that_holds_is_achieved_only_if_the_verifier_agrees(
    agrees, state
):
    done = await _drive_beats(FIRST, [agrees] * 2, {}, max_rounds=2)
    first = done.attempts[0]
    assert first.held == {"better.produced": True}  # Kept one, dropped the other two.
    assert len(first.produced["better.yes"]) == 1
    assert len(first.produced["better.no"]) == 2
    assert done.state == state


async def test_a_beats_reference_plan_whose_reference_was_never_scored_fails_the_round():
    unscored = Dna(sequence="ATGTCCTAA")  # Not one of the inputs, so it has no score.
    calls: dict = {}
    done = await _drive_beats(unscored, [True] * 2, calls, max_rounds=1)
    (att,) = done.attempts
    assert "Score the reference" in (att.error or "") and att.outcome is None
    assert att.critique == CRITIQUE and "verify" not in calls  # Nothing to verify.
    assert done.state == "not achieved" and _none_achieved(done)


async def test_a_criterion_nothing_can_meet_is_not_achieved_when_the_verifier_agrees():
    # No input has fewer TCG than the one with none, and a tie is not better.
    lowest = Dna(sequence="ATGTCTTAA")
    done = await _drive_beats(lowest, [True] * 2, {}, max_rounds=2)
    assert done.state == "not achieved" and _none_achieved(done)
    for a in done.attempts:
        assert a.held == {"better.produced": False} and a.produced["better.yes"] == []
        assert a.verdict and a.verdict.agrees and "did not hold" in a.verdict.reason
