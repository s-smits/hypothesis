import asyncio
import uuid
from pathlib import Path

import click
from temporalio.client import Client

from node_dag.dag import DagInput
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.payloads import data_converter


async def _main(path: Path, address: str) -> None:
    inp = DagInput.model_validate_json(path.read_text())
    client = await Client.connect(address, data_converter=data_converter)
    out = await client.execute_workflow(
        DagWorkflow.run, inp, id=f"dag-{uuid.uuid4()}", task_queue=TASK_QUEUE
    )
    click.echo(out.model_dump_json(indent=2))


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--address", default="localhost:7233", help="Temporal server address.")
def main(path: Path, address: str) -> None:
    """Run the DagInput JSON file at PATH and print the DagOutput."""
    asyncio.run(_main(path, address))


if __name__ == "__main__":
    main()
