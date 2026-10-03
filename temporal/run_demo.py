"""Run the whole stack on this machine with one command, no setup.

Starts a Temporal server, a worker and the UI in one process, so there is nothing to
install and nothing to run in another terminal. By default the three agents are stubbed
with a canned plan, which means **no API key is needed** and the blocked-on-a-tool cycle
is still genuinely exercised: the first resolve reports the node missing, and the resolve
after you click "Tool added - resume" finds it.

Pass ``--model`` to use the real agents instead, which needs ``ANTHROPIC_API_KEY``.

    uv run python -m temporal.run_demo
    uv run python -m temporal.run_demo --model anthropic:claude-fable-5-1
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
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.dag import Dag
from node_dag.plan import Criterion, Critique, Plan, ToolRequest, Verdict
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
from temporal.hypothesis.models import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_VERIFY_MODEL,
    CriteriaInput,
    CriteriaOutput,
    CritiqueInput,
    CritiqueOutput,
    PlanInput,
    PlanOutput,
    ResolveInput,
    ResolveOutput,
    VerifyInput,
)
from temporal.hypothesis.workflow import HypothesisWorkflow
from temporal.ui.app import make_app

GENE = "ATGTCGTCAGCTTAA"

#: What the stubbed builder "writes". Named for the demo goal below, so the plan it
#: produces actually fits what you typed in.
PLAN = Plan.model_validate(
    {
        "hypothesis": (
            "Recode every TCG and TCA codon to a synonym, then prove none survived. "
            "recode_codons is deterministic and targeted, so unlike mutate_synonymous "
            "it can actually remove a named codon."
        ),
        "expected": (
            "clean takes its yes branch: no TCG or TCA codon remains, and the protein "
            "is still MSSA*."
        ),
        "inputs": {"seq": "dna"},
        "steps": {
            "recoded": {
                "node": "recode_codons",
                "config": {"targets": ["TCG", "TCA"]},
                "inputs": {"sequence": "seq"},
                "why": "swap each target codon for a synonym of the same amino acid",
            },
            "clean": {
                "node": "codons_absent",
                "config": {"codons": ["TCG", "TCA"]},
                "inputs": {"sequence": "recoded"},
                "why": "a decision, so the branch it takes is recorded evidence",
            },
            "protein": {
                "node": "dna_to_protein",
                "config": {},
                "inputs": {"sequence": "clean.yes"},
                "why": "show which protein survived the recoding",
            },
        },
        "assertions": [
            {
                "criterion": "no_tcg_tca",
                "step": "clean",
                "branch": "yes",
                "claim": "no TCG or TCA codon remains in the recoded sequence",
            }
        ],
        "reasons": {
            "recode_codons": "targets named codons; mutate_synonymous picks at random",
            "codons_absent": "settles the criterion with a branch, not an opinion",
            "dna_to_protein": "shows the protein is unchanged",
        },
    }
)

#: The node the stubbed builder asks for, to show the blocked card and the resume cycle.
WANTED = ToolRequest.model_validate(
    {
        "name": "restriction_sites_absent",
        "node": "decision",
        "purpose": "Yes when a sequence carries none of the named restriction sites.",
        "category": "filter",
        "inputs": {"sequence": "dna"},
        "forwards": "sequence",
        "config_fields": [
            {
                "name": "low",
                "type": "float",
                "description": "Lowest GC fraction allowed",
            },
            {
                "name": "high",
                "type": "float",
                "description": "Highest GC fraction allowed",
            },
        ],
        "why_needed": (
            "The recoded gene has to be cloned, and nothing here can tell whether a "
            "recoding introduced a restriction site that would cut it."
        ),
        "why_not_composable": (
            "codons_absent only matches in frame, and a restriction site can sit "
            "across a codon boundary; restriction_sites_absent measures composition, not motifs."
        ),
        "example": "ATGTCTTAA with sites=[GAATTC] -> yes, forwarding the DNA on <step>.yes",
    }
)

BLOCKED = PLAN.model_copy(
    update={
        "steps": {
            **PLAN.steps,
            "gc": PLAN.steps["clean"].model_copy(
                update={
                    "node": "restriction_sites_absent",
                    "config": {"sites": ["GAATTC"]},
                    "inputs": {"sequence": "recoded"},
                    "why": "prove the recoding introduced no restriction site",
                }
            ),
        },
        "requests": {"restriction_sites_absent": WANTED},
    }
)

_seen: dict[str, int] = {}


def _rewire(plan: Plan, inputs: dict[str, str]) -> Plan:
    """Point a canned plan at whatever the hypothesis actually called its input.

    The real builder is told the input names and G0 rejects a plan that invents its
    own. A stub has no such discipline, so a plan written against ``seq`` produced a
    DAG the run could not be given its inputs for, which failed late and obscurely.
    """
    (want,) = list(inputs) or ["seq"]
    (have,) = list(plan.inputs)
    if want == have:
        return plan
    steps = {
        k: s.model_copy(
            update={
                "inputs": {p: (want if src == have else src) for p, src in s.inputs.items()}
            }
        )
        for k, s in plan.steps.items()
    }
    return plan.model_copy(update={"inputs": {want: plan.inputs[have]}, "steps": steps})


@activity.defn(name="plan_hypothesis")
async def stub_plan(inp: PlanInput) -> PlanOutput:
    """Hand back a canned plan that asks for a node nobody has written."""
    click.echo(f"  builder   plans for {inp.goal!r} ({len(inp.criteria)} criteria)")
    return PlanOutput(plan=_rewire(BLOCKED, inp.input_kinds), tokens=1200)


@activity.defn(name="resolve_plan")
def stub_resolve(inp: ResolveInput) -> ResolveOutput:
    """Report the node missing the first time, and present after a resume.

    Stands in for a human writing the node and restarting the worker, which is what the
    real ``resolve_plan`` would notice on its next call.
    """
    key = inp.plan.fingerprint()
    _seen[key] = _seen.get(key, 0) + 1
    if _seen[key] == 1:
        click.echo("  resolve   restriction_sites_absent is missing -> blocking for a human")
        return ResolveOutput(
            missing=[inp.plan.requests["restriction_sites_absent"]], registry_version="before"
        )
    click.echo("  resolve   restriction_sites_absent is here now -> running the DAG")
    # Same rewiring as the plan stub: the DAG has to declare the input name the
    # hypothesis actually used, or it cannot be given its inputs.
    runnable = _rewire(PLAN, inp.plan.inputs)
    return ResolveOutput(
        dag=Dag.model_validate(
            {
                "inputs": runnable.inputs,
                "steps": {k: s.draft() for k, s in runnable.steps.items()},
            }
        ),
        registry_version="after",
    )


@activity.defn(name="verify_outcome")
async def stub_verify(inp: VerifyInput) -> Verdict:
    """Agree, which on its own never makes a run successful."""
    click.echo(f"  verifier  assertions held: {inp.view.held}")
    return Verdict(
        agrees=True,
        covers_goal=True,
        reason=(
            "The recoded sequence contains no TCG or TCA codon, and dna_to_protein "
            "gives MSSA*, the same protein as the input."
        ),
        score=1.0,
    )


@activity.defn(name="critique_attempt")
async def stub_critique(inp: CritiqueInput) -> CritiqueOutput:
    """Only reached when a round is not accepted."""
    return CritiqueOutput(
        critique=Critique(
            diagnosis="a target codon survived the recoding step",
            root_cause="wrong_config",
            evidence=["clean.no"],
            fix="target every listed codon, not only the first",
            reason="the clean step took its no branch, so a target codon survived",
        )
    )


@activity.defn(name="derive_criteria")
async def stub_criteria(inp: CriteriaInput) -> CriteriaOutput:
    """Hand back the criterion the canned plan asserts, when none were typed.

    The real agent reads the goal. This one cannot, so it returns the criterion the
    stubbed plan happens to assert -- otherwise the run would end ``unverified``,
    correctly but confusingly, because nothing asserted whatever you did type.
    """
    click.echo("  criteria  none given, so using the one the canned plan asserts")
    return CriteriaOutput(
        criteria=[
            Criterion(
                id="no_tcg_tca",
                claim="no TCG or TCA codon remains",
                source="derived",
            )
        ],
        tokens=300,
    )


STUBS = [stub_plan, stub_resolve, stub_verify, stub_critique, stub_criteria]
REAL = [
    plan_hypothesis,
    resolve_plan,
    verify_outcome,
    critique_attempt,
    derive_criteria,
]


async def _main(host: str, port: int, model: str | None, results: Path) -> None:
    """Start Temporal, a worker and the UI, and serve until interrupted."""
    os.environ.setdefault("NODE_DAG_RESULTS", str(results))
    results.mkdir(parents=True, exist_ok=True)
    agents = REAL if model else STUBS
    click.echo("Starting a local Temporal server (the first run downloads it)...")
    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter
    ) as env:
        with ThreadPoolExecutor() as pool:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[DagWorkflow, HypothesisWorkflow],
                activities=[
                    run_tool,
                    run_decision,
                    save_workflow,
                    save_hypothesis_state,
                    save_requests,
                    *agents,
                ],
                activity_executor=pool,
            ):
                build = model or "demo"
                verify = DEFAULT_VERIFY_MODEL if model else "demo"
                app = make_app(env.client, build, verify, build)
                _banner(host, port, model, results)
                await uvicorn.Server(
                    uvicorn.Config(app, host=host, port=port, log_level="warning")
                ).serve()


def _banner(host: str, port: int, model: str | None, results: Path) -> None:
    """Print what to open and what to type into it."""
    url = f"http://{host}:{port}"
    click.echo("")
    click.echo(f"  Open  {url}/new")
    click.echo("")
    if model:
        click.echo(f"  Agents: {model} (real calls, so this costs tokens)")
    else:
        click.echo("  Agents: stubbed, so no API key is needed and nothing is charged.")
        click.echo("  Try this, to see a run block on a tool and then resume:")
        click.echo("")
        click.echo("    Goal       Remove every TCG and TCA codon without changing")
        click.echo("               the protein")
        click.echo("    Criterion  no_tcg_tca / no TCG or TCA codon remains")
        click.echo(f"    Input      seq / dna / {GENE}")
        click.echo("")
        click.echo("  The builder asks for a node nobody has written, so the run")
        click.echo("  blocks and shows you its contract. Click 'Tool added - resume'")
        click.echo("  and the held plan runs without being written again.")
    click.echo("")
    click.echo(f"  Results in {results}/   Ctrl-C to stop.")
    click.echo("")


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to serve the UI on.")
@click.option("--port", default=8000, help="Port to serve the UI on.")
@click.option(
    "--real",
    is_flag=True,
    help="Use the real agents on the default models. Needs ANTHROPIC_API_KEY.",
)
@click.option(
    "--model",
    help="Use the real agents with this pydantic-ai model, e.g. "
    "anthropic:claude-fable-5-1. Needs ANTHROPIC_API_KEY. Default: stubbed agents.",
)
@click.option(
    "--results",
    default="results-demo",
    type=click.Path(path_type=Path),
    help="Where to keep this demo's results, so it does not mix with your real runs.",
)
def main(
    host: str, port: int, real: bool, model: str | None, results: Path
) -> None:
    """Run Temporal, a worker and the UI together, for trying the loop by hand."""
    logging.basicConfig(level=logging.WARNING)
    model = model or (DEFAULT_REASONING_MODEL if real else None)
    try:
        asyncio.run(_main(host, port, model, results))
    except KeyboardInterrupt:
        click.echo("\nStopped.")


if __name__ == "__main__":
    main()
