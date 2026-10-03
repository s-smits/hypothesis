"""End to end through a real browser: the form, the loop, and the blocked card.

The whole stack runs in-process -- a real Temporal server from ``WorkflowEnvironment``, a
real worker, the real FastAPI app and the real DAG nodes. Only the three model calls are
faked, at the activity boundary, so these tests need no API key and still exercise every
line of JavaScript the pages actually ship.
"""

import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest
import uvicorn
from playwright.async_api import async_playwright
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.plan import Critique, Plan, ToolRequest, Verdict
from node_dag.types import TYPES
from temporal.dag.activities import run_decision, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.activities import (
    resolve_plan,
    save_hypothesis_state,
    save_requests,
)
from temporal.hypothesis.models import (
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

PLAN = Plan.model_validate(
    {
        "hypothesis": "recode the sequence, then check no TCG or TCA survived",
        "expected": "the clean step takes its yes branch, so both targets are gone",
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
                "why": "prove no target codon survived the recoding",
            },
        },
        "assertions": [
            {
                "criterion": "no_tcg",
                "step": "clean",
                "branch": "yes",
                "claim": "no TCG or TCA codon remains",
            }
        ],
        "reasons": {
            "recode_codons": "it targets named codons, unlike the random mutator",
            "codons_absent": "a decision settles the criterion with a branch",
        },
    }
)

WANTED = ToolRequest.model_validate(
    {
        "name": "gc_in_range",
        "node": "decision",
        "purpose": "Yes when the GC fraction of a sequence is within a range.",
        "category": "filter",
        "inputs": {"sequence": "dna"},
        "forwards": "sequence",
        "config_fields": [
            {"name": "low", "type": "float", "description": "Lowest GC allowed"}
        ],
        "why_needed": "the goal needs a GC window and nothing measures base composition",
        "why_not_composable": "codons_absent only tests whole codons, not composition",
        "example": "ATGGCC with low=0.4 high=0.8 -> yes",
    }
)

BLOCKED_PLAN = PLAN.model_copy(
    update={
        "steps": {
            **PLAN.steps,
            "clean": PLAN.steps["clean"].model_copy(update={"node": "gc_in_range"}),
        },
        "requests": {"gc_in_range": WANTED},
    }
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fakes(plan: Plan, verdict: Verdict, resolver=None) -> list:
    """The three model calls, faked at the activity boundary."""

    @activity.defn(name="plan_hypothesis")
    async def plan_hypothesis(inp: PlanInput) -> PlanOutput:
        return PlanOutput(plan=plan)

    @activity.defn(name="verify_outcome")
    async def verify_outcome(inp: VerifyInput) -> Verdict:
        return verdict

    @activity.defn(name="critique_attempt")
    async def critique_attempt(inp: CritiqueInput) -> CritiqueOutput:
        return CritiqueOutput(
            critique=Critique(
                diagnosis="a target codon survived the recoding step",
                root_cause="wrong_config",
                evidence=["clean.no"],
                fix="target every listed codon, not only the first",
                reason="the clean step took its no branch",
            )
        )

    return [plan_hypothesis, verify_outcome, critique_attempt, resolver or resolve_plan]


class _Stack:
    """A running UI with its Temporal server and worker, for one test."""

    def __init__(self, url: str, env: WorkflowEnvironment) -> None:
        self.url = url
        self.env = env


async def _serve(fakes: list):
    """Start Temporal, a worker and the UI, and yield the stack."""
    async with await WorkflowEnvironment.start_time_skipping(
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
                    *fakes,
                ],
                activity_executor=pool,
            ):
                port = _free_port()
                app = make_app(env.client, "fake-build", "fake-verify", "fake-critique")
                server = uvicorn.Server(
                    uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
                )
                task = asyncio.create_task(server.serve())
                while not server.started:
                    await asyncio.sleep(0.05)
                try:
                    yield _Stack(f"http://127.0.0.1:{port}", env)
                finally:
                    server.should_exit = True
                    await task


@pytest.fixture
async def page():
    """A real Chromium page."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        p = await browser.new_page()
        yield p
        await browser.close()


async def _start(page, stack: _Stack, criterion: str = "no_tcg") -> None:
    """Fill in the new-hypothesis form the way a person would, and submit it.

    The page seeds one input row and one criterion row once it has fetched /api/kinds,
    so wait for the dropdown before touching anything.
    """
    await page.goto(f"{stack.url}/new")
    # An <option> is never "visible" to Playwright, so wait for it to be attached.
    await page.wait_for_selector("select[name=kind] option", state="attached")
    await page.fill("#goal", "remove every TCG and TCA codon")
    await page.fill("input[name=cid]", criterion)
    await page.fill("input[name=claim]", "no TCG or TCA codon remains")
    await page.fill("input[name=name]", "seq")
    await page.select_option("select[name=kind]", "dna")
    await page.fill("input[name=value]", GENE)
    await page.click("#go")
    await page.wait_for_url("**/hypotheses?id=*", timeout=15000)


async def test_a_hypothesis_runs_from_the_form_to_an_accepted_verdict(page):
    """The whole path: form, workflow, real DAG, verdict, rendered."""
    verdict = Verdict(agrees=True, covers_goal=True, reason="both targets gone", score=1.0)
    async for stack in _serve(_fakes(PLAN, verdict)):
        await _start(page, stack)
        await page.wait_for_selector("text=achieved", timeout=20000)
        body = await page.inner_text("body")
        assert "recode the sequence" in body
        # The acceptance panel is the point: criterion, the assertion, and that it held.
        assert "no_tcg" in body
        assert "ATGTCTTCTGCTTAA" in body, "the recoded sequence never reached the page"


async def test_a_missing_tool_shows_the_contract_and_a_resume_button(page):
    """The blocked card is what a person acts on, so check it actually renders."""
    calls = {"n": 0}

    @activity.defn(name="resolve_plan")
    def resolver(inp: ResolveInput) -> ResolveOutput:
        calls["n"] += 1
        if calls["n"] == 1:
            return ResolveOutput(
                missing=[inp.plan.requests["gc_in_range"]], registry_version="before"
            )
        from node_dag.dag import Dag

        return ResolveOutput(
            dag=Dag.model_validate(
                {
                    "inputs": PLAN.inputs,
                    "steps": {k: s.draft() for k, s in PLAN.steps.items()},
                }
            ),
            registry_version="after",
        )

    verdict = Verdict(agrees=True, covers_goal=True, reason="ok", score=1.0)
    async for stack in _serve(_fakes(BLOCKED_PLAN, verdict, resolver)):
        await _start(page, stack)
        await page.wait_for_selector("text=blocked", timeout=20000)
        detail = page.url
        body = await page.inner_text("body")
        assert "gc_in_range" in body
        assert "why_not_composable" in body or "codons_absent only tests" in body
        assert "scaffold_node" in body, "the scaffold command is missing"

        # The requests page should rank it, with this run listed as blocked.
        await page.goto(f"{stack.url}/requests")
        await page.wait_for_selector("text=gc_in_range", timeout=10000)
        assert "1 hypothes" in (await page.inner_text("body"))

        # Now the human's click: resume, and the run finishes without re-planning.
        await page.goto(detail)
        await page.wait_for_selector("text=Tool added", timeout=15000)
        await page.click("text=Tool added")
        await page.wait_for_selector("text=achieved", timeout=20000)
        assert calls["n"] >= 2


async def test_a_plan_that_checks_nothing_is_shown_as_unverified(page):
    """Acceptance, through the browser: an agreeable verifier is not enough."""
    naked = PLAN.model_copy(update={"assertions": []})
    verdict = Verdict(agrees=True, covers_goal=True, reason="looks right", score=1.0)
    async for stack in _serve(_fakes(naked, verdict)):
        await _start(page, stack)
        await page.wait_for_selector("text=unverified", timeout=20000)
        body = await page.inner_text("body")
        assert "achieved" not in body.replace("not achieved", "")


async def test_the_new_form_offers_the_kinds_the_server_actually_has(page):
    """The dropdown is built from TYPES, so it cannot drift from the code again."""
    verdict = Verdict(agrees=True, covers_goal=True, reason="ok", score=1.0)
    async for stack in _serve(_fakes(PLAN, verdict)):
        await page.goto(f"{stack.url}/new")
        await page.wait_for_selector(
            "select[name=kind] option", state="attached"
        )
        kinds = await page.eval_on_selector_all(
            "select[name=kind] option", "els => els.map(e => e.value || e.textContent)"
        )
        assert set(kinds) == set(TYPES), kinds


async def test_every_page_loads_without_a_console_error(page):
    """A thrown exception in these pages shows as a blank panel, so catch it here."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    verdict = Verdict(agrees=True, covers_goal=True, reason="ok", score=1.0)
    async for stack in _serve(_fakes(PLAN, verdict)):
        await _start(page, stack)
        await page.wait_for_selector("text=achieved", timeout=20000)
        for path in ["/", "/hypotheses", "/requests", "/new"]:
            await page.goto(f"{stack.url}{path}")
            await page.wait_for_timeout(600)
        assert errors == [], errors
