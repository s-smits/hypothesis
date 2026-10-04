import asyncio
import logging
from pathlib import Path

import click
import uvicorn
from dotenv import load_dotenv
from temporalio.client import Client

from temporal.hypothesis.loop import BUILD_MODEL, VERIFY_MODEL
from temporal.payloads import data_converter
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
    client = await Client.connect(address, data_converter=data_converter)
    app = make_app(client, model, verify_model)
    logger = logging.getLogger(__name__)
    logger.info("Starting UI server on %s:%d with model=%s", host, port, model)
    await uvicorn.Server(uvicorn.Config(app, host=host, port=port)).serve()


@click.command()
@click.option("--address", default="localhost:7233", help="Temporal server address.")
@click.option("--host", default="127.0.0.1", help="Host to serve the UI on.")
@click.option("--port", default=8000, help="Port to serve the UI on.")
@click.option(
    "--model", default=BUILD_MODEL, show_default=True, help="Model for the builder."
)
@click.option(
    "--verify-model",
    default=VERIFY_MODEL,
    show_default=True,
    help="Model for the verifier. Keep it different from the builder's.",
)
def main(address: str, host: str, port: int, model: str, verify_model: str) -> None:
    """Serve pages that show DagWorkflow runs and hypotheses, and start hypotheses."""
    asyncio.run(_main(address, host, port, model, verify_model))


if __name__ == "__main__":
    main()
