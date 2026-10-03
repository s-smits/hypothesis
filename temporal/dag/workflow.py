import asyncio
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from node_dag.dag import DagInput, DagOutput, DagProgress, StepStatus
    from node_dag.nodes.base import BaseFilterConfig, BaseScoreConfig
    from node_dag.types import Table
    from temporal.dag.activities import (
        RunNodeInput,
        SaveWorkflowInput,
        run_filter,
        run_score,
        run_tool,
        save_workflow,
    )

TASK_QUEUE = "node-dag"


@workflow.defn
class DagWorkflow:
    """Run every step once its input exists, on all the entities that reach it.

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
            ((port, src),) = step.inputs.items()
            table = values[src]
            outs = (
                [f"{key}.yes", f"{key}.no"]
                if isinstance(config, BaseFilterConfig)
                else [key]
            )
            if not table.items:  # Nothing reached this step, so nothing comes out.
                values.update({out: Table() for out in outs})
                steps[key] = "skipped"
                return
            node_inp = RunNodeInput(config=config, inputs={port: table.items})
            timeout = timedelta(minutes=5)
            steps[key] = "running"
            try:
                if isinstance(config, BaseScoreConfig):
                    rows = await workflow.execute_activity(
                        run_score, node_inp, start_to_close_timeout=timeout
                    )
                    new = {
                        col: {i.id: r[name].value for i, r in zip(table.items, rows)}
                        for name, col in config.columns().items()
                    }
                    values[key] = Table(
                        items=table.items, scores={**table.scores, **new}
                    )
                elif isinstance(config, BaseFilterConfig):
                    node_inp = node_inp.model_copy(
                        update={
                            "values": [
                                table.scores[config.column][i.id] for i in table.items
                            ]
                        }
                    )
                    keep = await workflow.execute_activity(
                        run_filter, node_inp, start_to_close_timeout=timeout
                    )
                    for out, want in zip(outs, (True, False)):
                        values[out] = Table.of(
                            [i for i, k in zip(table.items, keep) if k == want],
                            table.scores,
                        )
                else:
                    items = await workflow.execute_activity(
                        run_tool, node_inp, start_to_close_timeout=timeout
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
        finally:
            # Save failed runs too, so you can see which step failed.
            await workflow.execute_activity(
                save_workflow,
                SaveWorkflowInput(
                    workflow_id=workflow.info().workflow_id, progress=self.progress()
                ),
                start_to_close_timeout=timedelta(seconds=30),
            )
        skipped = sorted(k for k, s in steps.items() if s == "skipped")
        return DagOutput(values=values, skipped=skipped)
