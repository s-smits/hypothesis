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
    """Hand back a fixed plan, and say in the plan itself that it is fixed.

    The one thing this cannot do is read the goal. Saying so where the plan is
    displayed beats a note in the terminal nobody scrolls back to, because the
    hypothesis text is the first thing anyone reads on the page.
    """
    click.echo(f"  builder   fixed plan, ignoring goal {inp.goal!r}")
    plan = _rewire(BLOCKED, inp.input_kinds)
    return PlanOutput(
        plan=plan.model_copy(
            update={
                "hypothesis": (
                    "STUBBED BUILDER: this plan is fixed and was not written for your "
                    "goal. Run with --real to have a model read it. The plan below "
                    "recodes TCG and TCA and checks none survive.\n\n"
                    + plan.hypothesis
                )
            }
        ),
        tokens=1200,
    )


@activity.defn(name="verify_outcome")
async def stub_verify(inp: VerifyInput) -> Verdict:
    """Report whether the assertions held, which is a fact rather than an opinion.

    A real verifier reads the outcome and forms a view. This one cannot, so instead of
    inventing one it states what the DAG did: every assertion either fired or it did
    not, and ``held`` already says which. Acceptance was never the model's to grant
    anyway, so a stub that only reports the checks is not far off the real contract.
    """
    held = inp.view.held
    ok = bool(held) and all(held.values())
    failed = sorted(k for k, v in held.items() if not v)
    click.echo(f"  verifier  assertions held: {held or 'none to check'}")
    if not held:
        reason = (
            "This plan asserted nothing, so there was nothing to check. Stubbed "
            "verifier: it reports the assertions rather than judging the outcome."
        )
    elif ok:
        reason = (
            f"Every assertion held: {sorted(held)}. Stubbed verifier: it reports what "
            "the DAG's decision steps did and forms no opinion of its own."
        )
    else:
        reason = (
            f"These assertions did not hold: {failed}. Stubbed verifier: it reports "
            "what the DAG's decision steps did and forms no opinion of its own."
        )
    return Verdict(agrees=ok, covers_goal=True, reason=reason, score=1.0 if ok else 0.0)


@activity.defn(name="critique_attempt")
async def critique_attempt_stub(inp: CritiqueInput) -> CritiqueOutput:
    """Repeat the failure rather than invent a diagnosis.

    Diagnosing is the one thing a stub genuinely cannot fake: the canned critique this
    replaced blamed a surviving codon for an input-name mismatch, and sent the next
    round after the wrong thing. Restating the evidence is less useful than a real
    critique and is at least true.
    """
    failed = sorted(k for k, v in inp.view.held.items() if not v)
    error = inp.view.error
    click.echo(f"  critic    error={error!r} unheld={failed}")
    if error:
        diagnosis = f"The round did not finish: {error}"
        cause, evidence = "node_raised", [error[:200]]
    elif failed:
        diagnosis = f"These assertions did not hold: {failed}"
        cause, evidence = "wrong_config", failed
    else:
        diagnosis = "The verifier did not accept the outcome, and no assertion failed."
        cause, evidence = "goal_misread", ["no failing assertion"]
    return CritiqueOutput(
        critique=Critique(
            diagnosis=diagnosis,
            root_cause=cause,
            evidence=evidence,
            fix=(
                "Stubbed critic: it restates the failure rather than diagnosing it. "
                "Run with --real for a critique that reads the attempt."
            ),
            reason=(
                "This is what the attempt reported. A stub cannot work out why, so it "
                "does not pretend to."
            ),
        )
    )


@activity.defn(name="derive_criteria")
async def stub_criteria(inp: CriteriaInput) -> CriteriaOutput:
    """Hand back the criterion the canned plan asserts, when none were typed.

    The real agent reads the goal. This one cannot, so it returns the criterion the
    stubbed plan happens to assert -- otherwise the run would end ``unverified``,
    correctly but confusingly, because nothing asserted whatever you did type.
    """
    click.echo("  criteria  none given, so using the one the fixed plan asserts")
    return CriteriaOutput(
        criteria=[
            Criterion(
                id="no_tcg_tca",
                claim=(
                    "no TCG or TCA codon remains (stubbed: fixed, not read from "
                    "your goal)"
                ),
                source="derived",
            )
        ],
        tokens=300,
    )


# resolve_plan is the real one: whether a node exists is a fact, and the fixed plan
# asks for one that genuinely is not there, so the blocked path still shows itself.
STUBS = [
    stub_plan,
    resolve_plan,
    stub_verify,
    critique_attempt_stub,
    stub_criteria,
]
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
