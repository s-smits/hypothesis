import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env")
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from node_dag.agent import Hypothesis
from temporal.dag.workflow import TASK_QUEUE
from temporal.hypothesis.loop import (
    BUILD_MODEL,
    VERIFY_MODEL,
    HypothesisInput,
    HypothesisLoop,
)


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--model",
    default=BUILD_MODEL,
    show_default=True,
    help="pydantic-ai model for the builder and critic.",
)
@click.option(
    "--verify-model",
    default=VERIFY_MODEL,
    show_default=True,
    help="pydantic-ai model for the verifier. Keep it different from the builder's.",
)
@click.option("--max-rounds", default=3, help="Most plans to try.")
@click.option(
    "--allow-requests",
    is_flag=True,
    help="Let the builder ask for a node nobody has written, and wait for a person to "
    "write it. Off by default: the builder composes from the nodes that exist.",
)
@click.option("--address", default="localhost:7233", help="Temporal server address.")
def main(
    path: Path,
    model: str,
    verify_model: str,
    max_rounds: int,
    allow_requests: bool,
    address: str,
) -> None:
    """Run the Hypothesis JSON file at PATH to a verdict. Print the result."""

    async def run() -> None:
        client = await Client.connect(address, data_converter=pydantic_data_converter)
        hyp = Hypothesis.model_validate_json(path.read_text())
        inp = HypothesisInput(
            hypothesis=hyp,
            build_model=model,
            verify_model=verify_model,
            max_rounds=max_rounds,
            allow_requests=allow_requests,
        )
        done = await client.execute_workflow(
            HypothesisLoop.run, inp, id=hyp.id, task_queue=TASK_QUEUE
        )
        click.echo(done.model_dump_json(indent=2))

    asyncio.run(run())


if __name__ == "__main__":
    main()
