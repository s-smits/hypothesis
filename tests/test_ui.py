import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from node_dag.agent import Criterion, Hypothesis
from node_dag.dag import Dag, DagProgress
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.dag.activities import SavedRun, SaveWorkflowInput
from temporal.run_hypothesis import (
    hypotheses_dir,
    registry_dir,
    run_hypothesis,
    save_hypothesis,
)
from temporal.ui.app import NEW, NewCriteria, NewHypothesis, make_app


def _endpoint(path: str):
    # These routes do not use the Temporal client.
    app = make_app(cast(Client, None))
    return next(
        r.endpoint
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == path and "GET" in r.methods
    )


def _post(app, path: str):
    return next(
        r.endpoint
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == path and "POST" in r.methods
    )


def _reply(info: AgentInfo, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


class _NoTemporal:
    """A client whose server is down, as when Temporal has been restarted."""

    def _down(self, *args, **kwargs):
        raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")

    list_workflows = describe = _down

    def get_workflow_handle(self, workflow_id: str):
        return self


async def test_finished_runs_show_without_temporal(results_dir):
    dag = Dag.model_validate(
        {
            "inputs": {"seq": "dna"},
            "steps": {
                "protein": {
                    "config": {"name": "dna_to_protein"},
                    "inputs": {"sequence": "seq"},
                }
            },
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    saved = SavedRun(
        dag=dag,
        steps={"protein": "failed"},
        values={},
        status="FAILED",
        start_time=start,
        close_time=start + timedelta(minutes=1),
        error="out of GPUs",
    )
    path = SaveWorkflowInput.path_for("new")
    path.parent.mkdir(parents=True)
    path.write_text(saved.model_dump_json())
    # A file from before runs saved their status and times.
    old = DagProgress(dag=dag, steps={"protein": "done"}, values={})
    SaveWorkflowInput.path_for("old").write_text(old.model_dump_json())
    app = make_app(cast(Client, _NoTemporal()))
    routes = {r.path: r.endpoint for r in app.routes if isinstance(r, APIRoute)}

    runs = await routes["/api/runs"]()

    assert [(r.id, r.status) for r in runs] == [("old", "COMPLETED"), ("new", "FAILED")]
    detail = await routes["/api/runs/{workflow_id}"]("new")
    assert detail.status == "FAILED"
    assert detail.error == "out of GPUs"
    assert detail.close_time == start + timedelta(minutes=1)
    assert detail.progress and detail.progress.steps == {"protein": "failed"}
    with pytest.raises(HTTPException) as e:
        await routes["/api/runs/{workflow_id}"]("never-saved")
    assert e.value.status_code == 503


def _dag() -> Dag:
    return Dag.model_validate(
        {
            "inputs": {"seq": "dna"},
            "steps": {
                "protein": {
                    "config": {"name": "dna_to_protein"},
                    "inputs": {"sequence": "seq"},
                }
            },
        }
    )


async def test_the_ui_marks_hypotheses_left_in_progress_interrupted(results_dir):
    inputs = {"seq": [Dna(sequence="ATG")]}
    building = Hypothesis(id="building", goal="g", inputs=inputs)
    running = building.model_copy(
        update={"id": "running", "dag": _dag(), "workflow_id": "running"}
    )
    # Its run failed and saved so, so it failed, whatever happened to the process.
    failed = running.model_copy(update={"id": "failed", "workflow_id": "failed"})
    saved = SavedRun(dag=_dag(), steps={"protein": "failed"}, values={})
    saved = saved.model_copy(update={"status": "FAILED", "error": "out of GPUs"})
    path = SaveWorkflowInput.path_for("failed")
    path.parent.mkdir(parents=True)
    path.write_text(saved.model_dump_json())
    for h in (building, running, failed):
        save_hypothesis(h)
    old = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    os.utime(hypotheses_dir() / "building.json", (old, old))

    rows = {r.hypothesis.id: r for r in await _endpoint("/api/hypotheses")()}

    assert {k: r.status for k, r in rows.items()} == {
        "building": "interrupted",
        "running": "interrupted",
        "failed": "failed",
    }
    assert rows["building"].hypothesis.error == (
        "Interrupted while building: the process running it stopped before it "
        "finished."
    )
    assert rows["building"].updated.timestamp() == old  # When it last got anywhere.
    assert rows["failed"].progress and rows["failed"].progress.error == "out of GPUs"


async def test_a_failed_build_saves_why(results_dir):
    def give_up(messages, info):
        raise RuntimeError("model is down")

    hyp = Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]})
    with pytest.raises(RuntimeError):
        await run_hypothesis(hyp, cast(Client, None), FunctionModel(give_up), "test")

    (row,) = await _endpoint("/api/hypotheses")()
    assert row.status == "failed"
    assert row.hypothesis.error == "Failed while building: RuntimeError: model is down"


async def test_a_cancelled_hypothesis_is_saved_interrupted(results_dir):
    started = asyncio.Event()

    async def hang(messages, info):
        started.set()
        await asyncio.Event().wait()

    hyp = Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]})
    task = asyncio.create_task(
        run_hypothesis(hyp, cast(Client, None), FunctionModel(hang), "test")
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    saved = Hypothesis.model_validate_json(
        (hypotheses_dir() / f"{hyp.id}.json").read_bytes()
    )
    assert saved.interrupted
    assert saved.error == "Interrupted while building: it was cancelled."


async def test_nodes_api_lists_what_the_builder_registered(results_dir):
    assert await _endpoint("/api/nodes")() == []
    score = OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA")
    column = score.columns()["expression"]
    registry = Registry(registry_dir())
    registry.register(score, "score expression")
    registry.register(AtMostConfig(column=column, threshold=400), "small ones")

    nodes = await _endpoint("/api/nodes")()

    scorer, filt = sorted(
        nodes, key=lambda n: n["config"]["name"] != "ostir_expression"
    )
    assert scorer["node"] == f"ostir_expression__{score.config_hash}"
    assert scorer["description"] == "score expression"
    assert scorer["categories"] == ["scoring"]
    assert scorer["score_columns"]["expression"] == column
    assert filt["filters_on"] == column
    assert filt["categories"] == ["filter"]


async def test_nodes_page_is_served_and_linked_from_every_page():
    page = await _endpoint("/nodes")()
    assert isinstance(page, FileResponse)
    ui = Path(page.path).parent
    assert "/api/nodes" in (ui / "nodes.html").read_text()
    for name in ("index", "hypotheses", "new", "nodes"):
        assert (ui / f"{name}.html").read_text().count('<a href="/nodes"') == 1


async def test_the_runs_page_can_show_a_structure():
    page = await _endpoint("/")()
    index = (Path(page.path).parent / "index.html").read_text()
    # A row with a structure gets a button, which loads Mol* and shows that structure.
    assert "data-view-id" in index
    assert "showStructure" in index
    assert "molstar" in index
    # The viewer is 5 MB, so the page does not load it until someone asks to see a
    # structure: it is fetched in loadMolstar, not by a script tag in the page.
    assert "<script src=" in index  # nice-dag is loaded up front, Mol* is not.
    assert not re.search(r"<(script|link)[^>]*molstar", index)


async def test_the_criteria_endpoint_drafts_a_list_to_edit():
    """The agent's draft comes back for the user to edit before they start a run."""
    seen: list[ModelMessage] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(messages)
        return _reply(
            info,
            {
                "response": [
                    {"kind": "quantitative", "text": "twice the expression"},
                    {"kind": "qualitative", "text": "the protein is unchanged"},
                ]
            },
        )

    app = make_app(cast(Client, None), build_model=FunctionModel(script))
    got = await _post(app, "/api/criteria")(
        NewCriteria(goal="faster lacZ", hypothesis="mutate codons")
    )

    assert got == [
        Criterion(kind="quantitative", text="twice the expression"),
        Criterion(kind="qualitative", text="the protein is unchanged"),
    ]
    prompt = next(p.content for p in seen[0].parts if isinstance(p, UserPromptPart))
    assert "Goal: faster lacZ" in str(prompt)
    assert "Proposed hypothesis: mutate codons" in str(prompt)

    with pytest.raises(HTTPException) as e:  # No model, no agent.
        await _post(make_app(cast(Client, None)), "/api/criteria")(
            NewCriteria(goal="faster lacZ")
        )
    assert e.value.status_code == 503


async def test_a_new_hypothesis_keeps_the_criteria_the_user_sent(results_dir):
    def give_up(messages, info):
        raise RuntimeError("stop before Temporal")

    app = make_app(cast(Client, None), build_model=FunctionModel(give_up))
    criteria = [Criterion(kind="qualitative", text="the protein is unchanged")]

    hyp = await _post(app, "/api/hypotheses")(
        NewHypothesis(goal="g", criteria=criteria)
    )
    await asyncio.sleep(0)  # Let the doomed background task settle.

    assert hyp.criteria == criteria


def test_the_new_page_edits_criteria_and_can_draft_them_with_the_agent():
    page = NEW.read_text()
    for s in ("Success criteria", "quantitative", "qualitative", "Add criterion"):
        assert s in page
    assert "/api/criteria" in page  # The "draft with the agent" button's call.
    assert "criteria: criteria()" in page  # They are sent when the run starts.


def test_a_hypothesis_starts_without_inputs_and_the_page_does_not_ask_for_them():
    """The builder agent chooses what to run on, so the form only takes words."""
    new = NewHypothesis(goal="increase the expression of E. coli lacZ")
    assert new.inputs == {}
    assert Hypothesis(goal=new.goal, inputs=new.inputs).inputs == {}

    page = NEW.read_text()
    assert "add_input" not in page and 'id="inputs"' not in page
    assert '"inputs"' not in page  # The request body carries no inputs.
