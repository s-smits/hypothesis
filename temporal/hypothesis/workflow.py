"""The loop: plan, resolve, run, verify, critique, repeat.

Durable because the hard part is waiting for a person. Registering a node means editing
``factory.py`` and restarting the worker, so a run blocked on a missing tool has to
survive that restart and however many days pass before someone writes it. A Temporal
signal plus ``wait_condition`` does that natively, holding no activity and no timer while
it waits.

Nothing here decides success. ``accepted`` does, from criteria frozen before any plan was
written and assertions the DAG actually satisfied. The verifier's opinion can only block.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy

with workflow.unsafe.imports_passed_through():
    from node_dag.dag import DagInput
    from node_dag.plan import (
        Attempt,
        AttemptSummary,
        Critique,
        Hypothesis,
        Plan,
        Verdict,
        accepted,
        holds,
    )
    from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
    from temporal.hypothesis.activities import (
        critique_attempt,
        derive_criteria,
        plan_hypothesis,
        resolve_plan,
        save_hypothesis_state,
        save_requests,
        verify_outcome,
    )
    from temporal.hypothesis.models import (
        CriteriaInput,
        CritiqueInput,
        HypothesisInput,
        PlanInput,
        ResolveInput,
        ResolveOutput,
        SaveHypothesisInput,
        SaveRequestsInput,
        VerifyInput,
        VerifyView,
        preview,
    )

# Model calls are slow and occasionally flaky. Two attempts, not Temporal's unlimited
# default: a 529 deserves a retry, a model that cannot comply should not burn tokens
# forever. Giving up returns a structured error, which the critique can work with.
AGENT = {
    "start_to_close_timeout": timedelta(minutes=10),
    "retry_policy": RetryPolicy(maximum_attempts=2),
}
QUICK = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}


@workflow.defn
class HypothesisWorkflow:
    """Run a goal to a verdict, asking for a tool and waiting when one is missing."""

    @workflow.init
    def __init__(self, inp: HypothesisInput) -> None:
        """Take the goal and settings, and start in the building state."""
        self._cfg = inp
        self._hyp = inp.hypothesis.model_copy(update={"state": "building"})
        self._added = False
        self._abandoned = False
        self._notes: list[str] = []

    @workflow.query
    def state(self) -> Hypothesis:
        """The Hypothesis as it stands, the same model the hypotheses page reads."""
        return self._hyp

    @workflow.signal
    def tool_added(self, note: str | None = None) -> None:
        """A requested node is now registered. Re-resolve against this worker.

        Args:
            note: Anything the person wants recorded with the resume.
        """
        self._added = True
        if note:
            self._notes.append(note)

    @workflow.signal
    def abandon(self, note: str | None = None) -> None:
        """Give up on this hypothesis at the next checkpoint.

        Args:
            note: Why it was abandoned.
        """
        self._abandoned = True
        if note:
            self._notes.append(note)

    async def _save(self) -> None:
        """Write the Hypothesis so the pages can follow along."""
        await workflow.execute_activity(
            save_hypothesis_state, SaveHypothesisInput(hypothesis=self._hyp), **QUICK
        )

    def _update(self, **fields: object) -> None:
        """Replace the Hypothesis with a copy carrying ``fields``."""
        self._hyp = self._hyp.model_copy(update=fields)

    def _put(self, att: Attempt) -> None:
        """Record this round, replacing the entry for it if there already is one.

        The attempt goes in as soon as it has a plan, not when the round ends, so a run
        that blocks waiting for a tool still shows what it intends to do -- otherwise the
        criteria panel reports that nothing asserts them while the plan sits right there.
        """
        kept = [a for a in self._hyp.attempts if a.round != att.round]
        self._update(attempts=[*kept, att])

    async def _stop(self, state: str, why: str) -> Hypothesis:
        """End the run in ``state``, recording why, and save it."""
        self._update(state=state, stopped_because=why, pending=[])
        await self._save()
        return self._hyp

    def _history(self) -> list[AttemptSummary]:
        """Every attempt so far, complete on reasoning and compact on data."""
        out = []
        for a in self._hyp.attempts:
            if a.plan is None:
                continue
            out.append(
                AttemptSummary(
                    round=a.round,
                    fingerprint=a.fingerprint,
                    hypothesis=a.plan.hypothesis,
                    expected=a.plan.expected,
                    assertions=a.plan.assertions,
                    held=holds(a.plan.assertions, a.outcome),
                    steps={
                        k: {"node": s.node, "config": s.config, "inputs": s.inputs}
                        for k, s in a.plan.steps.items()
                    },
                    requested=sorted(a.plan.requests),
                    verdict=a.verdict,
                    critique=a.critique,
                    error=a.error,
                    produced={
                        k: preview(v)
                        for k, v in (a.outcome.values if a.outcome else {}).items()
                    },
                )
            )
        return out

    def _spend(self, tokens: int) -> None:
        """Add an activity's token use to the running total."""
        used = dict(self._hyp.usage)
        used["total"] = used.get("total", 0) + tokens
        self._update(usage=used)

    async def _criteria(self) -> bool:
        """Derive the criteria when none were given. False when that failed.

        Runs once, before any plan, so the agent that plans cannot choose what it will be
        marked against.
        """
        if self._hyp.criteria:
            return True
        out = await workflow.execute_activity(
            derive_criteria,
            CriteriaInput(
                goal=self._hyp.goal,
                input_kinds=self._hyp.input_kinds(),
                model=self._cfg.build_model,
                tag=f"{self._hyp.id}-criteria",
            ),
            **AGENT,
        )
        self._spend(out.tokens)
        if not out.criteria:
            return False
        self._update(criteria=out.criteria)
        await self._save()
        return True

    async def _resolve(self, att: Attempt, plan: Plan) -> ResolveOutput:
        """Resolve the plan, blocking for a human while anything it names is missing.

        Each pass calls the activity again on purpose. A replay after a worker restart
        reuses the first call's recorded answer, so only a fresh invocation can see a
        node that has just been registered.
        """
        seen: set[str] = set()
        while True:
            res = await workflow.execute_activity(
                resolve_plan, ResolveInput(plan=plan), **QUICK
            )
            if res.error or not (res.missing or res.mismatched):
                return res
            att.requests = res.missing
            await workflow.execute_activity(
                save_requests,
                SaveRequestsInput(
                    hypothesis_id=self._hyp.id,
                    goal=self._hyp.goal,
                    round=att.round,
                    requests=res.missing,
                    mismatched=res.mismatched,
                ),
                **QUICK,
            )
            if res.registry_version in seen:
                att.resumes.append(
                    f"still missing {[r.name for r in res.missing]}: this worker has the "
                    f"same {len(res.available)} nodes as before, so it was not restarted"
                )
            for name, actual in res.mismatched.items():
                want = plan.requests[name].contract()
                att.resumes.append(
                    f"{name} exists but its ports are {actual.inputs} -> "
                    f"{actual.output or actual.forwards}, not the requested "
                    f"{want.inputs} -> {want.output or want.forwards}"
                )
            seen.add(res.registry_version)
            self._update(state="blocked", pending=res.missing)
            await self._save()
            await workflow.wait_condition(lambda: self._added or self._abandoned)
            if self._abandoned:
                return res
            self._added = False

    async def _verify(self, att: Attempt, plan: Plan) -> Verdict:
        """Judge the round, then decide acceptance from the criteria, not the model."""
        held = holds(plan.assertions, att.outcome)
        view = VerifyView(
            goal=self._hyp.goal,
            criteria=self._hyp.criteria,
            inputs=self._hyp.inputs,
            hypothesis=plan.hypothesis,
            expected=plan.expected,
            assertions=plan.assertions,
            held=held,
            dag=att.dag,
            outcome=att.outcome,
            error=att.error,
        )
        verdict = await workflow.execute_activity(
            verify_outcome,
            VerifyInput(
                view=view,
                model=self._cfg.verify_model,
                tag=f"{self._hyp.id}-r{att.round}-verify",
            ),
            **AGENT,
        )
        ok, why = accepted(self._hyp.criteria, plan, verdict, att.outcome)
        return verdict.model_copy(
            update={
                "achieved": ok,
                "prediction_held": bool(held) and all(held.values()),
                "reason": f"{verdict.reason}\n\nAcceptance: {why}",
            }
        )

    async def _critique(self, att: Attempt, plan: Plan) -> Critique | None:
        """Ask what to change next, when a round did not meet the goal."""
        out = await workflow.execute_activity(
            critique_attempt,
            CritiqueInput(
                view=VerifyView(
                    goal=self._hyp.goal,
                    criteria=self._hyp.criteria,
                    inputs=self._hyp.inputs,
                    hypothesis=plan.hypothesis,
                    expected=plan.expected,
                    assertions=plan.assertions,
                    held=holds(plan.assertions, att.outcome),
                    dag=att.dag,
                    outcome=att.outcome,
                    error=att.error,
                ),
                verdict=att.verdict,
                history=self._history(),
                model=self._cfg.critique_model,
                tag=f"{self._hyp.id}-r{att.round}-critique",
            ),
            **AGENT,
        )
        self._spend(out.tokens)
        return out.critique

    @workflow.run
    async def run(self, inp: HypothesisInput) -> Hypothesis:
        """Loop until the goal is met, the rounds run out, or a person stops it.

        Anything that escapes the loop is recorded before it is re-raised. A workflow
        that dies without writing its state leaves the page saying "building" forever,
        which is how a worker missing an activity looked indistinguishable from a model
        taking its time.
        """
        try:
            return await self._loop(inp)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._stop("failed", f"the run could not continue: {e}")
            raise

    async def _loop(self, inp: HypothesisInput) -> Hypothesis:
        """Plan, resolve, run, verify and critique until one of the stops fires."""
        await self._save()
        if not await self._criteria():
            return await self._stop("failed", "no criteria could be derived for the goal")

        critique: Critique | None = None
        stale = 0
        for rnd in range(1, self._cfg.max_rounds + 1):
            if self._abandoned:
                return await self._stop("abandoned", "a person abandoned the run")
            if self._hyp.usage.get("total", 0) > self._cfg.max_tokens:
                return await self._stop(
                    "not achieved", f"spent more than {self._cfg.max_tokens} tokens"
                )

            att = Attempt(round=rnd, started=workflow.now())
            self._update(
                round=rnd,
                state="building",
                critique=critique,
                dag=None,
                outcome=None,
                verdict=None,
                workflow_id=None,
            )
            await self._save()

            planned = await workflow.execute_activity(
                plan_hypothesis,
                PlanInput(
                    goal=self._hyp.goal,
                    criteria=self._hyp.criteria,
                    input_kinds=self._hyp.input_kinds(),
                    input_preview={
                        k: preview(v) for k, v in self._hyp.inputs.items()
                    },
                    model=self._cfg.build_model,
                    proposed=inp.proposed,
                    critique=critique,
                    history=self._history(),
                    max_requests=self._cfg.max_requests,
                    tag=f"{self._hyp.id}-r{rnd}-plan",
                ),
                **AGENT,
            )
            self._spend(planned.tokens)
            if planned.plan is None:
                att.error = planned.error
                att.finished = workflow.now()
                self._put(att)
                critique = Critique(
                    diagnosis=planned.error or "the builder produced no plan",
                    root_cause="goal_misread",
                    evidence=["the builder ran out of output retries"],
                    fix="submit a plan that satisfies the guards it was shown",
                    reason="no plan was produced, so there is nothing else to diagnose",
                )
                continue

            plan = planned.plan
            att.plan, att.fingerprint = plan, plan.fingerprint()
            self._update(hypothesis=plan.hypothesis)
            self._put(att)

            res = await self._resolve(att, plan)
            if self._abandoned:
                self._put(att)
                return await self._stop("abandoned", "abandoned while blocked on a tool")
            self._update(state="running", pending=[])

            if res.error or res.dag is None:
                att.error = res.error or "the plan could not be built into a DAG"
            else:
                att.dag = res.dag
                att.workflow_id = f"{self._hyp.id}-r{rnd}"
                self._update(dag=res.dag, workflow_id=att.workflow_id)
                await self._save()
                try:
                    att.outcome = await workflow.execute_child_workflow(
                        DagWorkflow.run,
                        DagInput(dag=res.dag, inputs=self._hyp.inputs),
                        id=att.workflow_id,
                        task_queue=TASK_QUEUE,
                        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                        execution_timeout=timedelta(hours=1),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - a failed step is a critique input
                    att.error = str(e)

            if att.outcome is not None:
                self._update(outcome=att.outcome, state="verifying")
                await self._save()
                att.verdict = await self._verify(att, plan)
                self._update(verdict=att.verdict)
                if att.verdict.achieved:
                    att.finished = workflow.now()
                    self._put(att)
                    return await self._stop("achieved", att.verdict.reason)

            self._update(state="critiquing")
            await self._save()
            att.critique = critique = await self._critique(att, plan)
            score = att.verdict.score if att.verdict else 0.0
            stale = 0 if score > self._hyp.best_score else stale + 1
            att.finished = workflow.now()
            self._put(att)
            self._update(
                best_score=max(self._hyp.best_score, score), critique=critique
            )
            await self._save()

            if not plan.assertions:
                return await self._stop(
                    "unverified",
                    "the plan asserted nothing, so no criterion could be checked",
                )
            if stale >= self._cfg.patience and rnd >= 2:
                return await self._stop(
                    "not achieved",
                    f"the score did not improve on {self._hyp.best_score} for "
                    f"{stale} rounds",
                )

        return await self._stop(
            "not achieved", f"out of rounds after {self._cfg.max_rounds}"
        )
