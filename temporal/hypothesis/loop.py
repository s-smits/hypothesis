"""The loop: criteria, plan, resolve, run, verify, critique, repeat.

Durable because the hard part is waiting for a person: registering a node means editing
the code and restarting the worker, and a run blocked on one must survive that. Nothing
here decides success; ``accepted`` does, and the verifier's opinion can only veto.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypedDict

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ChildWorkflowError,
    is_cancelled_exception,
)

with workflow.unsafe.imports_passed_through():
    from node_dag.agent import Hypothesis
    from node_dag.dag import DagInput
    from node_dag.plan import (
        Attempt,
        Critique,
        HypothesisState,
        Verdict,
        VerifyOpinion,
        accepted,
        holds,
        preview,
    )
    from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
    from temporal.hypothesis.activities import (
        Out,
        Stage,
        critique_attempt,
        derive_criteria,
        draft_inputs,
        plan_hypothesis,
        resolve_plan,
        save_requests,
        save_state,
        verify_outcome,
    )


class Options(TypedDict):
    """The activity options every call here sets, typed so ``**`` into them type-checks."""

    start_to_close_timeout: timedelta
    retry_policy: RetryPolicy


# A model call may be flaky once; one that cannot comply should not burn tokens forever.
AGENT: Options = {
    "start_to_close_timeout": timedelta(minutes=10),
    "retry_policy": RetryPolicy(maximum_attempts=2),
}
QUICK: Options = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}


# The loop was tuned with these two. The verifier is a different model from the builder on
# purpose: the critique loop pushes the builder to satisfy it, so a blind spot they shared
# would pass every round.
BUILD_MODEL = "anthropic:claude-sonnet-5-5"
VERIFY_MODEL = "anthropic:claude-haiku-4-5"


class Declined(Exception):
    """A model refused the request, so the run ends: the same prompt would be refused again."""


class HypothesisInput(BaseModel):
    """Start a Hypothesis loop. Models are pydantic-ai model strings."""

    hypothesis: Hypothesis
    build_model: str = BUILD_MODEL
    verify_model: str = VERIFY_MODEL
    max_rounds: int = 20
    # A round has cost a median of 115,000 tokens, so this ends a run near round 4.
    max_tokens: int = 500_000


@workflow.defn
class HypothesisLoop:
    """Run a goal to a verdict, asking for a tool and waiting when one is missing."""

    @workflow.init
    def __init__(self, inp: HypothesisInput) -> None:
        """Start in the building state."""
        self._cfg = inp
        self._hyp = inp.hypothesis.model_copy(update={"state": "building"})
        self._added = self._abandoned = False

    @workflow.query
    def state(self) -> Hypothesis:
        """The Hypothesis as it stands, the model the hypotheses page reads."""
        return self._hyp

    @workflow.signal
    def tool_added(self) -> None:
        """A requested node is now registered on the worker: resolve the same plan again."""
        self._added = True

    @workflow.signal
    def abandon(self) -> None:
        """Give up at the next checkpoint."""
        self._abandoned = True

    async def _set(self, **fields: object) -> None:
        self._hyp = self._hyp.model_copy(update=fields)
        await workflow.execute_activity(save_state, self._hyp, **QUICK)

    async def _put(
        self, att: Attempt, state: HypothesisState | None = None, **fields: object
    ) -> Attempt:
        """Replace this round's attempt as it progresses, in one save with ``state`` if given.

        Saved as it goes, so a blocked round shows its plan.
        """
        att = att.model_copy(update=fields)
        rest = [a for a in self._hyp.attempts if a.round != att.round]
        await self._set(attempts=[*rest, att], **({"state": state} if state else {}))
        return att

    async def _ask(
        self, stage: str, activity: Callable[[Stage], Awaitable[Out]], model: str
    ) -> Out:
        out = await workflow.execute_activity(
            activity,
            Stage(hyp=self._hyp, model=model),
            **AGENT,
        )
        used = self._hyp.usage
        self._hyp = self._hyp.model_copy(
            update={
                "usage": {
                    **used,
                    stage: used.get(stage, 0) + out.tokens,
                    "total": used.get("total", 0) + out.tokens,
                }
            }
        )
        if out.declined:
            raise Declined(f"the model declined the {stage} request: {out.error}")
        return out

    async def _stop(self, state: HypothesisState, why: str) -> Hypothesis:
        await self._set(state=state, stopped_because=why)
        return self._hyp

    @workflow.run
    async def run(self, inp: HypothesisInput) -> Hypothesis:
        """Loop until the goal is met, the rounds run out, or a person stops it."""
        try:
            final = await self._loop(inp)
        except Declined as e:
            final = await self._stop("failed", str(e))
        except Exception as e:  # noqa: BLE001
            # Out of retries on an error that is not the model's answer, such as an API
            # error, or a bug here. End as failed, or the saved Hypothesis stays
            # "building" for good, and the SDK retries a bug in workflow code for ever.
            cause: BaseException = e
            while cause.__cause__:  # The activity's own error is at the bottom.
                cause = cause.__cause__
            what = (
                f"{e.activity_type} failed"
                if isinstance(e, ActivityError)
                else "the loop stopped"
            )
            final = await self._stop("failed", f"{what}: {cause}")
        # By name: ``temporal.ledger`` reads the run as pulse does.
        # A ledger that cannot be written never changes how the run ended.
        try:
            await workflow.execute_activity("record_ledger", final.id, **QUICK)
        except ActivityError as e:
            if is_cancelled_exception(e):  # A cancel is not a failed write.
                raise
            workflow.logger.warning("The ledger line was not written: %s", e)
        return final

    async def _loop(self, inp: HypothesisInput) -> Hypothesis:
        await self._set(max_rounds=inp.max_rounds, max_tokens=inp.max_tokens)
        if (
            not self._hyp.inputs
        ):  # The caller's inputs win: read the goal only if none came.
            out = await self._ask("inputs", draft_inputs, inp.build_model)
            if not out.inputs:
                # Nothing is fetched for a goal, so a goal that names an entity without
                # giving its value has none to take. Say so, rather than leaving the
                # reason as the empty error of an agent that did as it was told.
                why = (
                    f": {out.error}"
                    if out.error
                    else ". The goal must give the entities to run on, since nothing is "
                    "fetched for it: paste the sequence or structure into the goal, or "
                    "start the run with `inputs`."
                )
                return await self._stop(
                    "failed", f"no inputs could be taken from the goal{why}"
                )
            await self._set(inputs=out.inputs, input_sources=out.sources)
        if not self._hyp.criteria:
            out = await self._ask("criteria", derive_criteria, inp.build_model)
            if not out.criteria:
                return await self._stop(
                    "failed", f"no criteria could be derived: {out.error}"
                )
            await self._set(criteria=out.criteria)
        for rnd in range(1, inp.max_rounds + 1):
            if self._abandoned:
                return await self._stop("abandoned", "a person abandoned the run")
            if self._hyp.usage.get("total", 0) > inp.max_tokens:
                return await self._stop(
                    "not achieved", f"spent more than {inp.max_tokens} tokens"
                )
            # Earlier rounds keep their previews and drop the raw outcome, to keep this small.
            old = [a.model_copy(update={"outcome": None}) for a in self._hyp.attempts]
            await self._set(attempts=old, round=rnd, state="building")
            att = Attempt(round=rnd, started=workflow.now())
            planned = await self._ask("plan", plan_hypothesis, inp.build_model)
            if planned.plan is None:
                fix = Critique(
                    diagnosis=planned.error or "no plan",
                    root_cause="goal_misread",
                    evidence=["the builder ran out of output retries"],
                    fix="submit a plan that passes the guards it is shown",
                )
                await self._put(att, error=planned.error, critique=fix)
                continue
            # Set before the save in _put, so this round's observations save with its plan.
            self._hyp = self._hyp.model_copy(
                update={"observations": planned.observations}
            )
            att = await self._put(att, plan=planned.plan)

            resumed = False
            while True:  # Resolve, and block while a requested node is missing.
                res = await workflow.execute_activity(
                    resolve_plan, planned.plan, **QUICK
                )
                if not res.missing:
                    break
                note = res.error or (
                    "still missing: was the worker restarted after adding it?"
                    if resumed
                    else None
                )
                await workflow.execute_activity(save_requests, res.missing, **QUICK)
                att = await self._put(att, "blocked", requests=res.missing, error=note)
                await workflow.wait_condition(lambda: self._added or self._abandoned)
                if self._abandoned:
                    return await self._stop(
                        "abandoned", "abandoned while blocked on a tool"
                    )
                # Clear on waking, not before waiting: a Resume sent while the next
                # resolve runs must wake the next wait, not be lost.
                resumed, self._added = True, False
            wid = f"{self._hyp.id}-r{rnd}"
            att = await self._put(
                att,
                "running",
                requests=[],
                error=res.error,
                dag=res.dag,
                workflow_id=wid if res.dag else None,
            )

            if res.dag is not None:
                try:
                    out = await workflow.execute_child_workflow(
                        DagWorkflow.run,
                        DagInput(dag=res.dag, inputs=self._hyp.inputs),
                        id=wid,
                        task_queue=TASK_QUEUE,
                    )
                    att = await self._put(
                        att,
                        "verifying",
                        outcome=out,
                        held=holds(planned.plan.assertions, out),
                        produced=preview(out),
                    )
                except ChildWorkflowError as e:
                    # A failed step is a critique input, not a crash.
                    cause: BaseException = e
                    while cause.__cause__:  # The node's own error is at the bottom.
                        cause = cause.__cause__
                    att = await self._put(att, error=str(cause))

            if att.outcome is not None:
                judged = await self._ask("verify", verify_outcome, inp.verify_model)
                op = judged.opinion or VerifyOpinion(
                    agrees=False,
                    covers_goal=False,
                    reason=f"the verifier failed: {judged.error}",
                )
                ok, why = accepted(self._hyp.criteria, att.plan, op, att.outcome)
                verdict = Verdict(
                    achieved=ok,
                    reason=f"{op.reason}\n\nAcceptance: {why}",
                    agrees=op.agrees,
                    covers_goal=op.covers_goal,
                )
                att = await self._put(att, verdict=verdict)
                if ok:
                    return await self._stop("achieved", why)
            await self._set(state="critiquing")
            crit = await self._ask("critique", critique_attempt, inp.build_model)
            await self._put(att, critique=crit.critique)
        return await self._stop("not achieved", f"out of rounds after {inp.max_rounds}")
