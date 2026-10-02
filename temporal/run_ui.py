import asyncio

import click
import uvicorn
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from temporal.ui.app import make_app


async def _main(address: str, host: str, port: int) -> None:
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    config = uvicorn.Config(make_app(client), host=host, port=port)
    await uvicorn.Server(config).serve()


@click.command()
@click.option("--address", default="localhost:7233", help="Temporal server address.")
@click.option("--host", default="127.0.0.1", help="Host to serve the UI on.")
@click.option("--port", default=8000, help="Port to serve the UI on.")
def main(address: str, host: str, port: int) -> None:
    """Serve a page that shows the progress and status of DagWorkflow runs."""
    asyncio.run(_main(address, host, port))


if __name__ == "__main__":
    main()
