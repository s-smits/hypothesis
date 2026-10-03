import asyncio
import json
import logging
from pathlib import Path

import click
from dotenv import load_dotenv
from pydantic_ai.models import Model

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env")
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from node_dag.agent import Hypothesis, build_agent, verify_agent
from node_dag.dag import DagInput
from node_dag.registry import Registry
from temporal.dag.activities import results_root, write_atomic
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow

logger = logging.getLogger(__name__)


def hypotheses_dir() -> Path:
    """``$NODE_DAG_RESULTS/hypotheses``: one ``<hypothesis id>.json`` per Hypothesis."""
    return results_root() / "hypotheses"


def registry_dir() -> Path:
    """``$NODE_DAG_RESULTS/registry``: one ``<node id>.json`` per registered node."""
    return results_root() / "registry"


def save_hypothesis(hyp: Hypothesis) -> Hypothesis:
    """Write ``hyp`` to its file and return it."""
    path = hypotheses_dir() / f"{hyp.id}.json"
    write_atomic(path, hyp.model_dump_json(indent=2).encode())
    return hyp


async def run_hypothesis(
    hyp: Hypothesis,
    client: Client,
    build_model: Model | str,
    verify_model: Model | str,
) -> Hypothesis:
    """Write a hypothesis and DAG for ``hyp.goal``, run it, then verify the outcome.

    Returns a copy of ``hyp`` with the rest of its fields set. Saves it after each
    stage, so the UI can show how far it has got.
    """
    try:
        logger.info("Building hypothesis %s: %s", hyp.id, hyp.goal)
        save_hypothesis(hyp)
        prompt = f"Goal: {hyp.goal}\nInputs: {json.dumps(hyp.describe_inputs())}"
        if (
            hyp.hypothesis
        ):  # One the user proposed. The builder replaces it with its own.
            prompt += f"\nProposed hypothesis: {hyp.hypothesis}"
        logger.info("Calling builder agent for %s", hyp.id)
        agent = build_agent(build_model, Registry(registry_dir()))
        hyp = (await agent.run(prompt, deps=hyp)).output
        assert hyp.dag is not None
        logger.info(
            "Builder finished for %s, DAG has %d steps", hyp.id, len(hyp.dag.steps)
        )
        hyp = save_hypothesis(hyp.model_copy(update={"workflow_id": hyp.id}))

        logger.info("Running DAG workflow %s", hyp.id)
        outcome = await client.execute_workflow(
            DagWorkflow.run,
            DagInput(dag=hyp.dag, inputs=hyp.inputs),
            id=hyp.id,
            task_queue=TASK_QUEUE,
        )
        logger.info("DAG workflow finished for %s", hyp.id)
        hyp = save_hypothesis(hyp.model_copy(update={"outcome": outcome}))

        logger.info("Calling verifier agent for %s", hyp.id)
        verdict = await verify_agent(verify_model).run(
            hyp.model_dump_json(exclude={"verdict"})
        )
        logger.info(
            "Verifier finished for %s: achieved=%s", hyp.id, verdict.output.achieved
        )
        return save_hypothesis(hyp.model_copy(update={"verdict": verdict.output}))
    except Exception as e:
        logger.exception("Hypothesis %s failed", hyp.id)
        raise


async def _main(path: Path, model: str, verify_model: str | None, address: str) -> None:
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    hyp = Hypothesis.model_validate_json(path.read_text())
    done = await run_hypothesis(hyp, client, model, verify_model or model)
    click.echo(done.model_dump_json(indent=2))


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--model", required=True, help="pydantic-ai model for the builder.")
@click.option(
    "--verify-model", help="pydantic-ai model for the verifier. Default: --model."
)
@click.option("--address", default="localhost:7233", help="Temporal server address.")
def main(path: Path, model: str, verify_model: str | None, address: str) -> None:
    """Build, run and verify the Hypothesis JSON file at PATH. Print the result."""
    asyncio.run(_main(path, model, verify_model, address))


if __name__ == "__main__":
    main()
