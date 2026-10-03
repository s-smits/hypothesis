import asyncio
import logging
from pathlib import Path

import click
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env")
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from node_dag.plan import Hypothesis
from temporal.dag.workflow import TASK_QUEUE
from temporal.hypothesis.models import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_VERIFY_MODEL,
    HypothesisInput,
)
from temporal.hypothesis.workflow import HypothesisWorkflow
from temporal.store import hypotheses_dir, save_hypothesis

# These two now live in temporal.store, a leaf module the Temporal workflow can import
# without dragging in the UI or the agents. Re-exported here so callers that already
# import them from this module (temporal/ui/app.py, tests/test_agent.py) keep working.
__all__ = ["hypotheses_dir", "main", "run_hypothesis", "save_hypothesis"]

logger = logging.getLogger(__name__)


async def run_hypothesis(
    hyp: Hypothesis,
    client: Client,
    build_model: str,
    verify_model: str,
    critique_model: str | None = None,
    max_rounds: int = 3,
) -> Hypothesis:
    """Run ``hyp`` to a verdict as a HypothesisWorkflow, and return the finished record.

    The loop itself lives in the workflow, so it survives the worker restart that
    registering a new node needs. This is now a thin client over it.

    Args:
        hyp: The goal, inputs and criteria to run.
        client: The Temporal client.
        build_model: pydantic-ai model for the builder.
        verify_model: pydantic-ai model for the verifier.
        critique_model: pydantic-ai model for the critic. Default: ``build_model``.
        max_rounds: Plan-run-verify rounds before giving up.
    """
    logger.info("Starting hypothesis %s: %s", hyp.id, hyp.goal)
    save_hypothesis(hyp)
    return await client.execute_workflow(
        HypothesisWorkflow.run,
        HypothesisInput(
            hypothesis=hyp,
            proposed=hyp.hypothesis,
            build_model=build_model,
            verify_model=verify_model,
            critique_model=critique_model or build_model,
            max_rounds=max_rounds,
        ),
        id=hyp.id,
        task_queue=TASK_QUEUE,
    )


async def _main(
    path: Path,
    model: str,
    verify_model: str | None,
    critique_model: str | None,
    max_rounds: int,
    address: str,
) -> None:
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    hyp = Hypothesis.model_validate_json(path.read_text())
    done = await run_hypothesis(
        hyp, client, model, verify_model or model, critique_model, max_rounds
    )
    click.echo(done.model_dump_json(indent=2))


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--model",
    default=DEFAULT_REASONING_MODEL,
    show_default=True,
    help="pydantic-ai model for the builder.",
)
@click.option(
    "--verify-model",
    default=DEFAULT_VERIFY_MODEL,
    show_default=True,
    help="pydantic-ai model for the verifier. Keep it different from --model.",
)
@click.option(
    "--critique-model", help="pydantic-ai model for the critic. Default: --model."
)
@click.option(
    "--max-rounds", default=3, help="Plan-run-verify rounds before giving up."
)
@click.option("--address", default="localhost:7233", help="Temporal server address.")
def main(
    path: Path,
    model: str,
    verify_model: str | None,
    critique_model: str | None,
    max_rounds: int,
    address: str,
) -> None:
    """Build, run and verify the Hypothesis JSON file at PATH. Print the result."""
    asyncio.run(_main(path, model, verify_model, critique_model, max_rounds, address))


if __name__ == "__main__":
    main()
