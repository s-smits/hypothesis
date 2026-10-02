import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from types import FunctionType

import click
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from temporal.dag.activities import RunNodeInput, run_decision, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow


def _delayed(fn: FunctionType, seconds: float) -> FunctionType:
    """``fn`` as an activity of the same name that sleeps ``seconds`` first."""

    @activity.defn(name=fn.__name__)
    def run(inp: RunNodeInput) -> object:
        time.sleep(seconds)
        return fn(inp)

    return run


async def _main(address: str, step_delay: float) -> None:
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    activities = [run_tool, run_decision]
    if step_delay:
        activities = [_delayed(fn, step_delay) for fn in activities]
    activities.append(save_workflow)
    with ThreadPoolExecutor() as pool:
        await Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[DagWorkflow],
            activities=activities,
            activity_executor=pool,
        ).run()


@click.command()
@click.option("--address", default="localhost:7233", help="Temporal server address.")
@click.option(
    "--step-delay",
    default=0.0,
    help="Seconds each step sleeps before it runs, to watch progress in the UI.",
)
def main(address: str, step_delay: float) -> None:
    """Run a worker for DagWorkflow."""
    asyncio.run(_main(address, step_delay))


if __name__ == "__main__":
    main()
