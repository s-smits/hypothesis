"""The loop, with the agents faked at the activity boundary.

Every model call sits behind exactly one activity, so these tests override those by name
the way ``tests/test_dag.py`` already overrides ``run_tool``. No FunctionModel is needed
at this layer, and the DAG underneath is the real thing.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.plan import (
    Criterion,
    Critique,
    Hypothesis,
    Plan,
    ToolRequest,
    Verdict,
)
from node_dag.types import Dna
from temporal.dag.activities import run_decision, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.activities import (
    resolve_plan,
    save_hypothesis_state,
    save_requests,
)
from temporal.hypothesis.models import (
    CritiqueInput,
    CritiqueOutput,
    HypothesisInput,
    PlanInput,
    PlanOutput,
    ResolveInput,
    ResolveOutput,
    VerifyInput,
)
from temporal.hypothesis.workflow import HypothesisWorkflow
from temporal.store import hypotheses_dir

GENE = Dna(sequence="ATGTCGTCAGCTTAA")
NO_TCG = Criterion(id="no_tcg", claim="no TCG codon remains")

REAL_PLAN = Plan.model_validate(
    {
        "hypothesis": "recode the sequence, then check no TCG survived",
        "expected": "the clean step takes its yes branch",
        "inputs": {"seq": "dna"},
        "steps": {
            "recoded": {
                "node": "recode_codons",
                "config": {"targets": ["TCG"]},
                "inputs": {"sequence": "seq"},
                "why": "swap each TCG for a synonym",
            },
            "clean": {
                "node": "codons_absent",
                "config": {"codons": ["TCG"]},
                "inputs": {"sequence": "recoded"},
                "why": "prove no TCG survived",
            },
        },
        "assertions": [
            {
                "criterion": "no_tcg",
                "step": "clean",
                "branch": "yes",
                "claim": "no TCG remains",
            }
        ],
        "reasons": {"recode_codons": "targeted", "codons_absent": "settles it"},
    }
)

WANTED = ToolRequest.model_validate(
    {
        "name": "gc_in_range",
        "node": "decision",
        "purpose": "Yes when the GC fraction is within a range.",
        "category": "filter",
        "inputs": {"sequence": "dna"},
        "forwards": "sequence",
        "why_needed": "the goal needs a GC window and nothing measures GC content",
        "why_not_composable": "codons_absent only tests whole codons, not composition",
        "example": "ATGGCC with low=0.4 high=0.8 -> yes",
    }
)

CRITIQUE = Critique(
    diagnosis="the recoding left a target codon in place at the third position",
    root_cause="wrong_config",
    evidence=["clean.no"],
    fix="target every listed codon, not just the first one that appears",
    reason="the clean step took its no branch, so a target codon survived",
)


def _hyp(**fields: object) -> Hypothesis:
    return Hypothesis(
        goal="remove every TCG codon without changing the protein",
        criteria=[NO_TCG],
        inputs={"seq": GENE},
        **fields,
    )


def _input(hyp: Hypothesis, **cfg: object) -> HypothesisInput:
    return HypothesisInput(
        hypothesis=hyp,
        build_model="fake",
        verify_model="fake",
        critique_model="fake",
        **cfg,
    )


def _agrees(score: float = 1.0) -> Verdict:
    return Verdict(agrees=True, covers_goal=True, reason="matches", score=score)


async def _run(env: WorkflowEnvironment, acts: list, hyp: Hypothesis, **cfg: object):
    """Run the loop with the given fake activities, and return the final Hypothesis.

    ``resolve_plan`` is the real one: it reads the live node registry, which is exactly
    what these tests want checked. Only the model calls are faked.
    """
    with ThreadPoolExecutor() as pool:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[DagWorkflow, HypothesisWorkflow],
            activities=[
                run_tool,
                run_decision,
                save_workflow,
                save_hypothesis_state,
                save_requests,
                resolve_plan,
                *acts,
            ],
            activity_executor=pool,
        ):
            return await env.client.execute_workflow(
                HypothesisWorkflow.run,
                _input(hyp, **cfg),
                id=hyp.id,
                task_queue=TASK_QUEUE,
            )


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as e:
        yield e


async def _await_state(handle, want: str, tries: int = 400):
    """Poll the workflow's own query until it reports ``want``."""
    for _ in range(tries):
        if (await handle.query(HypothesisWorkflow.state)).state == want:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"never reached {want!r}")


async def _await_count(count, want: int, tries: int = 400):
    """Poll a counter until it reaches ``want``."""
    for _ in range(tries):
        if count() >= want:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"never reached {want}")


def _planner(calls: list, plan: Plan = REAL_PLAN):
    @activity.defn(name="plan_hypothesis")
    async def plan_hypothesis(inp: PlanInput) -> PlanOutput:
        calls.append(inp)
        return PlanOutput(plan=plan)

    return plan_hypothesis


def _verifier(verdicts: list[Verdict], seen: list | None = None):
    @activity.defn(name="verify_outcome")
    async def verify_outcome(inp: VerifyInput) -> Verdict:
        if seen is not None:
            seen.append(inp)
        return verdicts[min(len(seen or []) - 1 if seen else 0, len(verdicts) - 1)]

    return verify_outcome


def _critic(calls: list):
    @activity.defn(name="critique_attempt")
    async def critique_attempt(inp: CritiqueInput) -> CritiqueOutput:
        calls.append(inp)
        return CritiqueOutput(critique=CRITIQUE)

    return critique_attempt


async def test_a_plan_whose_nodes_all_exist_runs_and_is_accepted(env):
    plans, seen = [], []
    done = await _run(
        env,
        [_planner(plans), _verifier([_agrees()], seen), _critic([])],
        _hyp(),
    )
    assert done.state == "achieved", done.stopped_because
    assert len(plans) == 1
    assert len(done.attempts) == 1
    assert done.attempts[0].workflow_id == f"{done.id}-r1"
    assert done.verdict.achieved and done.verdict.prediction_held


async def test_a_missing_tool_blocks_then_resumes_without_replanning(env):
    """The central behaviour: the held plan is reused, not rewritten."""
    plans, resolves = [], []
    blocked_plan = REAL_PLAN.model_copy(
        update={
            "steps": {
                **REAL_PLAN.steps,
                "clean": REAL_PLAN.steps["clean"].model_copy(
                    update={"node": "gc_in_range"}
                ),
            },
            "requests": {"gc_in_range": WANTED},
        }
    )

    @activity.defn(name="resolve_plan")
    def resolve_plan(inp: ResolveInput) -> ResolveOutput:
        resolves.append(inp)
        if len(resolves) == 1:
            return ResolveOutput(
                missing=[inp.plan.requests["gc_in_range"]], registry_version="before"
            )
        # The human wrote the node and restarted the worker: resolve the real plan.
        from node_dag.dag import Dag

        dag = Dag.model_validate(
            {
                "inputs": REAL_PLAN.inputs,
                "steps": {k: s.draft() for k, s in REAL_PLAN.steps.items()},
            }
        )
        return ResolveOutput(dag=dag, registry_version="after")

    with ThreadPoolExecutor() as pool:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[DagWorkflow, HypothesisWorkflow],
            activities=[
                run_tool,
                run_decision,
                save_workflow,
                save_hypothesis_state,
                save_requests,
                _planner(plans, blocked_plan),
                resolve_plan,
                _verifier([_agrees()], []),
                _critic([]),
            ],
            activity_executor=pool,
        ):
            hyp = _hyp()
            handle = await env.client.start_workflow(
                HypothesisWorkflow.run, _input(hyp), id=hyp.id, task_queue=TASK_QUEUE
            )
            await _await_state(handle, "blocked")
            state = await handle.query(HypothesisWorkflow.state)
            assert [r.name for r in state.pending] == ["gc_in_range"]
            await handle.signal(HypothesisWorkflow.tool_added)
            done = await handle.result()

    assert done.state == "achieved", done.stopped_because
    assert len(plans) == 1, "the plan was rebuilt instead of being resumed"
    assert len(resolves) == 2


async def test_a_resume_without_restarting_the_worker_blocks_again(env):
    plans, resolves = [], []
    blocked_plan = REAL_PLAN.model_copy(
        update={
            "steps": {
                **REAL_PLAN.steps,
                "clean": REAL_PLAN.steps["clean"].model_copy(
                    update={"node": "gc_in_range"}
                ),
            },
            "requests": {"gc_in_range": WANTED},
        }
    )

    @activity.defn(name="resolve_plan")
    def resolve_plan(inp: ResolveInput) -> ResolveOutput:
        resolves.append(inp)
        if len(resolves) <= 2:  # same registry both times: nobody restarted anything
            return ResolveOutput(
                missing=[inp.plan.requests["gc_in_range"]], registry_version="same"
            )
        from node_dag.dag import Dag

        return ResolveOutput(
            dag=Dag.model_validate(
                {
                    "inputs": REAL_PLAN.inputs,
                    "steps": {k: s.draft() for k, s in REAL_PLAN.steps.items()},
                }
            ),
            registry_version="after",
        )

    with ThreadPoolExecutor() as pool:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[DagWorkflow, HypothesisWorkflow],
            activities=[
                run_tool,
                run_decision,
                save_workflow,
                save_hypothesis_state,
                save_requests,
                _planner(plans, blocked_plan),
                resolve_plan,
                _verifier([_agrees()], []),
                _critic([]),
            ],
            activity_executor=pool,
        ):
            hyp = _hyp()
            handle = await env.client.start_workflow(
                HypothesisWorkflow.run, _input(hyp), id=hyp.id, task_queue=TASK_QUEUE
            )
            await _await_state(handle, "blocked")
            await handle.signal(HypothesisWorkflow.tool_added)
            await _await_count(lambda: len(resolves), 2)
            await handle.signal(HypothesisWorkflow.tool_added)
            done = await handle.result()

    note = " ".join(done.attempts[0].resumes)
    assert "not restarted" in note, done.attempts[0].resumes
    assert len(plans) == 1


async def test_abandoning_a_blocked_run_stops_it(env):
    plans, resolves = [], []
    blocked_plan = REAL_PLAN.model_copy(
        update={
            "steps": {
                **REAL_PLAN.steps,
                "clean": REAL_PLAN.steps["clean"].model_copy(
                    update={"node": "gc_in_range"}
                ),
            },
            "requests": {"gc_in_range": WANTED},
        }
    )

    @activity.defn(name="resolve_plan")
    def resolve_plan(inp: ResolveInput) -> ResolveOutput:
        resolves.append(inp)
        return ResolveOutput(
            missing=[inp.plan.requests["gc_in_range"]], registry_version="before"
        )

    with ThreadPoolExecutor() as pool:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[DagWorkflow, HypothesisWorkflow],
            activities=[
                run_tool,
                run_decision,
                save_workflow,
                save_hypothesis_state,
                save_requests,
                _planner(plans, blocked_plan),
                resolve_plan,
                _verifier([_agrees()], []),
                _critic([]),
            ],
            activity_executor=pool,
        ):
            hyp = _hyp()
            handle = await env.client.start_workflow(
                HypothesisWorkflow.run, _input(hyp), id=hyp.id, task_queue=TASK_QUEUE
            )
            await _await_state(handle, "blocked")
            await handle.signal(HypothesisWorkflow.abandon, "not worth a new node")
            done = await handle.result()

    assert done.state == "abandoned"
    assert done.stopped_because


async def test_a_rejected_round_feeds_its_critique_into_the_next_plan(env):
    plans, seen, critiques = [], [], []
    done = await _run(
        env,
        [
            _planner(plans),
            _verifier([Verdict(agrees=False, reason="a TCG survived", score=0.2),
                       _agrees()], seen),
            _critic(critiques),
        ],
        _hyp(),
        max_rounds=2,
    )
    assert len(plans) == 2
    assert plans[1].critique is not None, "the critique was not carried forward"
    assert len(plans[1].history) == 1, "the previous attempt was not carried forward"
    assert plans[1].history[0].fingerprint == REAL_PLAN.fingerprint()
    assert done.state == "achieved", done.stopped_because


async def test_the_loop_stops_when_the_score_stops_improving(env):
    plans, seen = [], []
    done = await _run(
        env,
        [
            _planner(plans),
            _verifier([Verdict(agrees=False, reason="no better", score=0.3)], seen),
            _critic([]),
        ],
        _hyp(),
        max_rounds=5,
        patience=2,
    )
    assert done.state == "not achieved"
    assert "did not improve" in done.stopped_because
    assert len(plans) < 5, "patience did not cut the run short"


async def test_the_verifier_never_sees_a_previous_verdict(env):
    """VerifyView is an allow-list, so the judge cannot read its own earlier opinions."""
    seen: list[VerifyInput] = []
    await _run(
        env,
        [
            _planner([]),
            _verifier(
                [Verdict(agrees=False, reason="no", score=0.2), _agrees()], seen
            ),
            _critic([]),
        ],
        _hyp(),
        max_rounds=2,
    )
    for call in seen:
        dumped = call.view.model_dump_json()
        assert "attempts" not in dumped
        assert "critique" not in dumped


async def test_a_plan_with_no_assertions_ends_unverified(env):
    """Acceptance: the verifier agreeing is not enough when nothing was checked."""
    naked = REAL_PLAN.model_copy(update={"assertions": []})
    done = await _run(
        env,
        [_planner([], naked), _verifier([_agrees()], []), _critic([])],
        _hyp(),
        max_rounds=1,
    )
    assert done.state == "unverified", done.stopped_because
    assert not done.verdict.achieved


async def test_a_node_that_raises_becomes_a_critique_not_a_crash(env):
    """mutate_synonymous with more swaps than swappable codons raises inside the DAG."""
    boom = Plan.model_validate(
        {
            **REAL_PLAN.model_dump(mode="json"),
            "steps": {
                "recoded": {
                    "node": "mutate_synonymous",
                    "config": {"seed": 1, "count": 99},
                    "inputs": {"sequence": "seq"},
                    "why": "ask for more swaps than the sequence can give",
                },
                "clean": REAL_PLAN.steps["clean"].model_dump(mode="json")
                | {"inputs": {"sequence": "recoded"}},
            },
        }
    )
    critiques: list[CritiqueInput] = []
    done = await _run(
        env,
        [_planner([], boom), _verifier([_agrees()], []), _critic(critiques)],
        _hyp(),
        max_rounds=1,
    )
    assert done.state in {"not achieved", "unverified"}, done.stopped_because
    assert critiques and critiques[0].view.error, "the failure never reached the critic"


async def test_a_worker_missing_an_activity_records_a_failure(env):
    """A crash must leave the page saying what went wrong, not "building" forever.

    This is the shape of a real misconfiguration: the demo worker registered every agent
    stub except derive_criteria, so a hypothesis submitted with no criteria called an
    activity that was not there. The run died, nothing wrote its state, and the page sat
    at "building" indefinitely -- indistinguishable from a model taking its time.
    """
    hyp = Hypothesis(
        goal="remove every TCG codon", criteria=[], inputs={"seq": GENE}
    )
    with pytest.raises(WorkflowFailureError):
        # No derive_criteria here, and the hypothesis has no criteria, so the workflow
        # must reach for an activity this worker does not have.
        await _run(
            env,
            [_planner([]), _verifier([_agrees()], []), _critic([])],
            hyp,
        )

    saved = Hypothesis.model_validate_json(
        (hypotheses_dir() / f"{hyp.id}.json").read_bytes()
    )
    assert saved.state == "failed", saved.state
    assert saved.stopped_because and "could not continue" in saved.stopped_because


async def test_a_plan_wired_to_the_wrong_input_name_says_so(env):
    """The builder named an input the goal never offered.

    This used to surface as a pydantic error about types, from DagInput, in the middle
    of a round -- so the critique diagnosed the wrong thing and the loop burned both its
    rounds on it. resolve_plan now catches it where the message can name the mistake.
    """
    hyp = Hypothesis(
        goal="remove every TCG codon",
        criteria=[NO_TCG],
        inputs={"sequence": GENE},  # the plan below wires to "seq"
    )
    critiques: list[CritiqueInput] = []
    done = await _run(
        env,
        [_planner([]), _verifier([_agrees()], []), _critic(critiques)],
        hyp,
        max_rounds=1,
    )
    assert done.attempts[0].error
    assert "the plan declares inputs" in done.attempts[0].error
    assert "sequence" in done.attempts[0].error
    assert critiques, "the critic never saw the failure"
    assert "declares inputs" in (critiques[0].view.error or "")
