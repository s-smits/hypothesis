import asyncio
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from node_dag.dag import DagInput, DagOutput, DagProgress, StepStatus
    from node_dag.nodes.base import BaseDecisionConfig
    from temporal.dag.activities import (
        RunNodeInput,
        SaveWorkflowInput,
        run_decision,
        run_tool,
        save_workflow,
    )

TASK_QUEUE = "node-dag"


@workflow.defn
class DagWorkflow:
    """Run every step once its inputs exist. Steps that are ready together run in parallel."""

    @workflow.init
    def __init__(self, inp: DagInput) -> None:
        self._dag = inp.dag
        self._values = dict(inp.inputs)
        self._steps: dict[str, StepStatus] = {k: "pending" for k in inp.dag.steps}

    @workflow.query
    def progress(self) -> DagProgress:
        """The status of each step and the values produced so far."""
        return DagProgress(dag=self._dag, steps=self._steps, values=self._values)

    @workflow.run
    async def run(self, inp: DagInput) -> DagOutput:
        """Run the DAG and return every value it produced."""
        dag, values, steps = self._dag, self._values, self._steps

        async def run_step(key: str) -> None:
            step = dag.steps[key]
            if any(src not in values for src in step.inputs.values()):
                steps[key] = "skipped"
                return
            node_inp = RunNodeInput(
                config=step.config,
                inputs={port: values[src] for port, src in step.inputs.items()},
            )
            timeout = timedelta(minutes=5)
            steps[key] = "running"
            try:
                if isinstance(step.config, BaseDecisionConfig):
                    yes = await workflow.execute_activity(
                        run_decision, node_inp, start_to_close_timeout=timeout
                    )
                    branch = f"{key}.{'yes' if yes else 'no'}"
                    values[branch] = node_inp.inputs[step.config.forwards]
                else:
                    values[key] = await workflow.execute_activity(
                        run_tool, node_inp, start_to_close_timeout=timeout
                    )
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
