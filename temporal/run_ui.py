import asyncio
import logging
from pathlib import Path

import click
import uvicorn
from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from temporal.ui.app import make_app

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def _main(
    address: str, host: str, port: int, model: str | None, verify_model: str | None
) -> None:
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    app = make_app(client, model, verify_model)
    logger = logging.getLogger(__name__)
    logger.info("Starting UI server on %s:%d with model=%s", host, port, model)
    await uvicorn.Server(uvicorn.Config(app, host=host, port=port)).serve()


@click.command()
@click.option("--address", default="localhost:7233", help="Temporal server address.")
@click.option("--host", default="127.0.0.1", help="Host to serve the UI on.")
@click.option("--port", default=8000, help="Port to serve the UI on.")
@click.option(
    "--model",
    help="pydantic-ai model for the builder. Needed to start hypotheses from the UI.",
)
@click.option(
    "--verify-model", help="pydantic-ai model for the verifier. Default: --model."
)
def main(
    address: str, host: str, port: int, model: str | None, verify_model: str | None
) -> None:
    """Serve pages that show DagWorkflow runs and hypotheses, and start hypotheses."""
    asyncio.run(_main(address, host, port, model, verify_model))


if __name__ == "__main__":
    main()
