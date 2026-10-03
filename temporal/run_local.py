"""Run the whole stack on this machine with one command.

Starts a Temporal server, a worker and the UI in one process, so there is no CLI to
install and nothing to run in a second terminal. The agents are the real ones, so it
needs ``ANTHROPIC_API_KEY`` in a ``.env`` at the repo root.

This replaced a stubbed demo that could run without a key. The stubs were not worth
what they cost: a fixed plan that ignored the goal, a verdict that named a protein the
run never produced, a resolver that reported a node missing without looking, and
criteria invented for a goal nobody read. Every one of those was reported as a bug in
the system when the system was fine. A launcher that calls the real agents cannot lie
about what the agents did.

    uv run python -m temporal.run_local
    uv run python -m temporal.run_local --model anthropic:claude-opus-5-5
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.factory import MAPPING
from temporal.dag.activities import run_decision, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.activities import (
    critique_attempt,
    derive_criteria,
    plan_hypothesis,
    resolve_plan,
    save_hypothesis_state,
    save_requests,
    verify_outcome,
)
from temporal.hypothesis.models import DEFAULT_REASONING_MODEL, DEFAULT_VERIFY_MODEL
from temporal.hypothesis.workflow import HypothesisWorkflow
from temporal.ui.app import make_app

ACTIVITIES = [
    run_tool,
    run_decision,
    save_workflow,
    save_hypothesis_state,
    save_requests,
    derive_criteria,
    plan_hypothesis,
    resolve_plan,
    verify_outcome,
    critique_attempt,
]


def _banner(host: str, port: int, build: str, verify: str, results: Path) -> None:
    """Say what is running, with what, and what it will cost."""
    nodes = sorted(c.model_fields["name"].default for c in MAPPING)
    click.echo("")
    click.echo(f"  Open  http://{host}:{port}/new")
    click.echo("")
    click.echo(f"  Builder and critic : {build}")
    click.echo(f"  Verifier           : {verify}")
    click.echo(f"  Nodes available    : {len(nodes)}")
    click.echo(f"    {', '.join(nodes)}")
    click.echo("")
    click.echo("  These are real model calls and they cost tokens.")
    click.echo("")
    click.echo("  Give it a goal, the inputs, and the criteria that decide whether it")
    click.echo("  worked. A run with no criteria ends unverified: nothing can be")
    click.echo("  checked against a success nobody defined.")
    click.echo("")
    click.echo(f"  Results in {results}/   Ctrl-C to stop.")
    click.echo("")


async def _main(
    host: str, port: int, build: str, verify: str, critic: str, results: Path
) -> None:
    """Start Temporal, a worker and the UI, and serve until interrupted."""
    os.environ.setdefault("NODE_DAG_RESULTS", str(results))
    results.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise click.ClickException(
            "No ANTHROPIC_API_KEY. Put it in a .env at the repo root; it is gitignored."
        )
    click.echo("Starting a local Temporal server (the first run downloads it)...")
    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter
    ) as env:
        with ThreadPoolExecutor() as pool:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[DagWorkflow, HypothesisWorkflow],
                activities=ACTIVITIES,
                activity_executor=pool,
            ):
                app = make_app(env.client, build, verify, critic)
                _banner(host, port, build, verify, results)
                await uvicorn.Server(
                    uvicorn.Config(app, host=host, port=port, log_level="warning")
                ).serve()


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to serve the UI on.")
@click.option("--port", default=8000, help="Port to serve the UI on.")
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
    "--critique-model", default=None, help="pydantic-ai model for the critic. "
    "Default: --model."
)
@click.option(
    "--results",
    default="results",
    type=click.Path(path_type=Path),
    help="Where to keep results.",
)
def main(
    host: str,
    port: int,
    model: str,
    verify_model: str,
    critique_model: str | None,
    results: Path,
) -> None:
    """Run Temporal, a worker and the UI together, for using the loop by hand."""
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(
            _main(host, port, model, verify_model, critique_model or model, results)
        )
    except KeyboardInterrupt:
        click.echo("\nStopped.")


if __name__ == "__main__":
    main()
