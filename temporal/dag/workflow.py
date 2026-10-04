import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from node_dag.dag import DagInput, DagOutput, DagProgress, StepStatus
    from node_dag.nodes.base import BaseFilterConfig, BaseScoreConfig
    from node_dag.types import Table
    from temporal.dag.activities import (
        RunNodeInput,
        SavedRun,
        SaveWorkflowInput,
        run_filter,
        run_score,
        run_tool,
        save_workflow,
    )

TASK_QUEUE = "node-dag"
# A node that raises will raise again, so give up fast rather than retry until timeout.
RETRY = RetryPolicy(maximum_attempts=3)


@workflow.defn
class DagWorkflow:
    """Run every step once its inputs exist, on all the entities that reach it.

    Steps that are ready together run in parallel.
    """

    @workflow.init
    def __init__(self, inp: DagInput) -> None:
        self._dag = inp.dag
        self._values = {k: Table.of(v) for k, v in inp.inputs.items()}
        self._steps: dict[str, StepStatus] = {k: "pending" for k in inp.dag.steps}

    @workflow.query
    def progress(self) -> DagProgress:
        """The status of each step and the tables produced so far."""
        return DagProgress(dag=self._dag, steps=self._steps, values=self._values)

    @workflow.run
    async def run(self, inp: DagInput) -> DagOutput:
        """Run the DAG and return every value it produced."""
        dag, values, steps = self._dag, self._values, self._steps

        async def run_step(key: str) -> None:
            step, config = dag.steps[key], dag.steps[key].config
            tables = {port: values[src] for port, src in step.inputs.items()}
            # Only a tool has more than one port, and it reads no scores, so a score
            # or filter can take the one table it has.
            table = next(iter(tables.values()))
            outs = (
                [f"{key}.yes", f"{key}.no"]
                if isinstance(config, BaseFilterConfig)
                else [key]
            )
            # A port with nothing to run on means nothing comes out.
            if not all(t.items for t in tables.values()):
                values.update({out: Table() for out in outs})
                steps[key] = "skipped"
                return
            node_inp = RunNodeInput(
                config=config, inputs={p: t.items for p, t in tables.items()}, step=key
            )
            # A node that runs on a GPU somewhere else needs longer than a local one,
            # so each config says how long its work may take.
            timeout = timedelta(minutes=config.timeout_minutes)
            steps[key] = "running"
            try:
                if isinstance(config, BaseScoreConfig):
                    rows = await workflow.execute_activity(
                        run_score,
                        node_inp,
                        start_to_close_timeout=timeout,
                        retry_policy=RETRY,
                    )
                    new = {
                        col: {i.id: r[name].value for i, r in zip(table.items, rows)}
                        for name, col in config.columns().items()
                    }
                    values[key] = Table(
                        items=table.items, scores={**table.scores, **new}
                    )
                elif isinstance(config, BaseFilterConfig):
                    cols = config.score_columns()
                    by_col = {
                        c: [table.scores[c][i.id] for i in table.items] for c in cols
                    }
                    # One column stays a bare list, so a single-column filter's
                    # activity input, and therefore its cache key, is unchanged.
                    node_inp = node_inp.model_copy(
                        update={"values": by_col[cols[0]] if len(cols) == 1 else by_col}
                    )
                    keep = await workflow.execute_activity(
                        run_filter,
                        node_inp,
                        start_to_close_timeout=timeout,
                        retry_policy=RETRY,
                    )
                    for out, want in zip(outs, (True, False)):
                        values[out] = Table.of(
                            [i for i, k in zip(table.items, keep) if k == want],
                            table.scores,
                        )
                else:
                    items = await workflow.execute_activity(
                        run_tool,
                        node_inp,
                        start_to_close_timeout=timeout,
                        retry_policy=RETRY,
                    )
                    values[key] = Table.of(items)
            except Exception:
                steps[key] = "failed"
                raise
            steps[key] = "done"

        # Mark each step done as soon as it finishes, so a step never waits for a
        # slow step it does not depend on.
        order, running = dag.order(), {}
        order.prepare()
        status, error = "COMPLETED", None
        try:
            while order.is_active():
                for key in order.get_ready():
                    if key in dag.steps:
                        running[asyncio.create_task(run_step(key))] = key
                    else:
                        order.done(key)  # A DAG input: already has its value.
                if running:
                    finished, _ = await workflow.wait(
                        running, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in finished:
                        task.result()
                        order.done(running.pop(task))
        except asyncio.CancelledError:
            status = "CANCELED"
            raise
        except Exception as e:
            cause: BaseException = e
            while cause.__cause__:  # The activity error wraps the node's error.
                cause = cause.__cause__
            status, error = "FAILED", str(cause)
            raise
        finally:
            # Save failed runs too, so you can see which step failed.
            saved = SavedRun(
                dag=dag,
                steps=steps,
                values=values,
                status=status,
                start_time=workflow.info().start_time,
                close_time=workflow.now(),
                error=error,
            )
            # A file that cannot be written never changes how the run ended: the caller
            # still gets the result, or the step's own error, and Temporal has the run.
            try:
                await workflow.execute_activity(
                    save_workflow,
                    SaveWorkflowInput(
                        workflow_id=workflow.info().workflow_id, progress=saved
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY,
                )
            except ActivityError as e:
                workflow.logger.warning("The run was not saved: %s", e.cause or e)
        skipped = sorted(k for k, s in steps.items() if s == "skipped")
        return DagOutput(values=values, skipped=skipped)
