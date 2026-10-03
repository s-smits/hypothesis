import asyncio
import json
import logging
from pathlib import Path

import click
from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.messages import RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
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


def _clip(value: object, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else f"{text[:limit]}… ({len(text)} chars)"


def _log_node(hyp_id: str, node: object) -> None:
    """Log what the builder just did, so a slow or looping build can be seen."""
    if Agent.is_model_request_node(node):
        for part in node.request.parts:
            if isinstance(part, ToolReturnPart):
                logger.info(
                    "[%s] %s returned %s", hyp_id, part.tool_name, _clip(part.content)
                )
            elif isinstance(part, RetryPromptPart):
                logger.warning(
                    "[%s] retry %s: %s",
                    hyp_id,
                    part.tool_name or "output",
                    _clip(part.content, 1000),
                )
        logger.info("[%s] waiting for the model", hyp_id)
    elif Agent.is_call_tools_node(node):
        for part in node.model_response.parts:
            if isinstance(part, ToolCallPart):
                logger.info(
                    "[%s] calls %s(%s)", hyp_id, part.tool_name, _clip(part.args)
                )
            elif isinstance(part, TextPart) and part.content.strip():
                logger.info("[%s] says %s", hyp_id, _clip(part.content))


def stage(hyp: Hypothesis) -> str:
    """The stage ``hyp`` is in, from what it has: the one its next save ends."""
    if hyp.outcome:
        return "verifying"
    return "running" if hyp.dag else "building"


def _reason(e: BaseException) -> str:
    """The innermost cause of ``e``, e.g. a node's error inside a workflow failure."""
    while e.__cause__:
        e = e.__cause__
    return f"{type(e).__name__}: {e}"


async def run_hypothesis(
    hyp: Hypothesis,
    client: Client,
    build_model: Model | str,
    verify_model: Model | str,
) -> Hypothesis:
    """Write a hypothesis and DAG for ``hyp.goal``, run it, then verify the outcome.

    Returns a copy of ``hyp`` with the rest of its fields set. Saves it after each
    stage, so the UI can show how far it has got. If a stage fails, saves why in
    ``error``; if the task is cancelled, e.g. because the UI is stopping, saves it
    as ``interrupted``.
    """
    # A rerun starts afresh, whatever stopped the last run.
    hyp = hyp.model_copy(update={"error": None, "interrupted": False})
    try:
        logger.info("Building hypothesis %s: %s", hyp.id, hyp.goal)
        save_hypothesis(hyp)
        prompt = f"Goal: {hyp.goal}\n" + (
            f"Inputs: {json.dumps(hyp.describe_inputs())}"
            if hyp.inputs
            else "Inputs: none given. Choose them from the goal and declare them "
            "with add_input."
        )
        if (
            hyp.hypothesis
        ):  # One the user proposed. The builder replaces it with its own.
            prompt += f"\nProposed hypothesis: {hyp.hypothesis}"
        logger.info("Calling builder agent for %s", hyp.id)
        agent = build_agent(build_model, Registry(registry_dir()))
        async with agent.iter(prompt, deps=hyp) as run:
            async for node in run:
                _log_node(hyp.id, node)
        assert run.result is not None
        hyp = run.result.output
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
            hyp.model_dump_json(exclude={"verdict", "error", "interrupted"})
        )
        logger.info(
            "Verifier finished for %s: achieved=%s", hyp.id, verdict.output.achieved
        )
        return save_hypothesis(hyp.model_copy(update={"verdict": verdict.output}))
    except asyncio.CancelledError:
        logger.warning("Hypothesis %s was interrupted", hyp.id)
        save_hypothesis(
            hyp.model_copy(
                update={
                    "interrupted": True,
                    "error": f"Interrupted while {stage(hyp)}: it was cancelled.",
                }
            )
        )
        raise
    except Exception as e:
        logger.exception("Hypothesis %s failed", hyp.id)
        save_hypothesis(
            hyp.model_copy(update={"error": f"Failed while {stage(hyp)}: {_reason(e)}"})
        )
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
