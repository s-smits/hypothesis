import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import FunctionType

import click
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env")
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from temporal.dag.activities import (
    RunNodeInput,
    run_filter,
    run_score,
    run_tool,
    save_workflow,
)
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis import activities as hyp
from temporal.hypothesis.loop import HypothesisLoop
from temporal.ledger import record_ledger
from temporal.payloads import data_converter


def _delayed(fn: FunctionType, seconds: float) -> FunctionType:
    """``fn`` as an activity of the same name that sleeps ``seconds`` first."""

    @activity.defn(name=fn.__name__)
    def run(inp: RunNodeInput) -> object:
        time.sleep(seconds)
        return fn(inp)

    return run


async def _main(address: str, step_delay: float) -> None:
    client = await Client.connect(address, data_converter=data_converter)
    activities: list[FunctionType] = [run_tool, run_score, run_filter]
    if step_delay:
        activities = [_delayed(fn, step_delay) for fn in activities]
    activities += [
        save_workflow,
        hyp.derive_criteria,
        hyp.draft_inputs,
        hyp.plan_hypothesis,
        hyp.resolve_plan,
        hyp.verify_outcome,
        hyp.critique_attempt,
        hyp.save_state,
        hyp.save_requests,
        record_ledger,
    ]
    with ThreadPoolExecutor() as pool:
        await Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[DagWorkflow, HypothesisLoop],
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
