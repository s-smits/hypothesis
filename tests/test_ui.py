import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from node_dag import amass
from node_dag.agent import Hypothesis
from node_dag.dag import Dag, DagProgress
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.plan import Attempt, Observation, ToolRequest
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.dag.activities import SavedRun, SaveWorkflowInput, results_subdir
from temporal.hypothesis.activities import save_hypothesis
from temporal.hypothesis.loop import HypothesisInput
from temporal.ui.app import NewHypothesis, _hypothesis_row, make_app


class _Handle:
    def __init__(self, client: "_Client", hyp_id: str) -> None:
        self.client, self.hyp_id = client, hyp_id

    async def signal(self, name: str) -> None:
        self.client.signals.append((self.hyp_id, name))


class _Client:
    """Records what the app asks Temporal to do."""

    def __init__(self) -> None:
        self.started: list[tuple[HypothesisInput, dict]] = []
        self.signals: list[tuple[str, str]] = []

    async def start_workflow(self, run, inp: HypothesisInput, **kw) -> None:
        self.started.append((inp, kw))

    def get_workflow_handle(self, hyp_id: str) -> _Handle:
        return _Handle(self, hyp_id)


def _endpoint(path: str, method: str = "GET", client=None, model=None):
    app = make_app(cast(Client, client), model)
    return next(
        r.endpoint
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == path and method in (r.methods or ())
    )


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


async def test_a_row_carries_the_saved_run_of_its_current_round(results_dir):
    saved = SavedRun(dag=_dag(), steps={"protein": "failed"}, values={})
    saved = saved.model_copy(update={"status": "FAILED", "error": "out of GPUs"})
    path = SaveWorkflowInput.path_for("h-r2")
    path.parent.mkdir(parents=True)
    path.write_text(saved.model_dump_json())
    inputs = {"seq": [Dna(sequence="ATG")]}
    attempts = [Attempt(round=2, dag=_dag(), workflow_id="h-r2")]
    # state is None for a file from before the loop, so its status comes from its run.
    for id_, state in (("old", None), ("loop", "running")):
        save_hypothesis(
            Hypothesis(
                id=id_, goal="g", inputs=inputs, round=2, attempts=attempts, state=state
            )
        )

    rows = {r.hypothesis.id: r for r in await _endpoint("/api/hypotheses")()}

    assert {k: r.status for k, r in rows.items()} == {
        "old": "failed",
        "loop": "running",
    }
    assert all(r.progress and r.progress.error == "out of GPUs" for r in rows.values())


async def test_an_observation_lists_the_hypotheses_that_cite_it(
    results_dir, monkeypatch
):
    record = {"amassId": "AMBC_1", "title": "Ribosome binding sites", "fulltext": "..."}
    monkeypatch.setattr(amass, "get_record", lambda core, amass_id, include=(): record)
    cited = Observation(
        amass_id="AMBC_1",
        summary="RBS strength sets expression.",
        core="biomedcore",
        title="Ribosome binding sites",
    )
    inputs = {"seq": [Dna(sequence="ATG")]}
    save_hypothesis(
        Hypothesis(id="cites", goal="g", inputs=inputs, observations=[cited])
    )
    save_hypothesis(Hypothesis(id="silent", goal="g", inputs=inputs))

    detail = await _endpoint("/api/observations/{core}/{amass_id}")(
        "biomedcore", "AMBC_1"
    )

    assert detail.record == record
    assert [(c.hypothesis_id, c.summary) for c in detail.cited_by] == [
        ("cites", "RBS strength sets expression.")
    ]


async def test_nodes_api_lists_what_the_builder_registered(results_dir):
    assert await _endpoint("/api/nodes")() == []
    score = OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA")
    column = score.columns()["expression"]
    registry = Registry(results_subdir("registry"))
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


async def test_starting_without_max_rounds_is_accepted_and_blank_criteria_are_dropped():
    client = _Client()
    new = NewHypothesis(
        goal="g",
        inputs={"seq": [Dna(sequence="ATG")]},
        criteria=["no TCG remains", "  "],
        max_rounds=None,
    )
    hyp = await _endpoint("/api/hypotheses", "POST", client, "model")(new)
    ((inp, kw),) = client.started
    assert (
        inp.max_rounds == 3 and kw["id"] == hyp.id
    )  # Blank means the default, not a 422.
    assert [c.claim for c in inp.hypothesis.criteria] == ["no TCG remains"]


async def test_requests_are_ranked_by_how_many_runs_they_block_and_resume_signals_a_run():
    ask: dict[str, Any] = {
        "purpose": "p",
        "kind": "dna",
        "why_needed": "w",
        "why_not_composable": "w",
        "example": "e",
    }
    wanted, other = (
        ToolRequest(name=n, node="tool", output="dna", **ask)
        for n in ("wanted", "other")
    )
    for r in (other, wanted):
        write = results_subdir("requests") / f"{r.name}.json"
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(r.model_dump_json())
    inputs = {"seq": [Dna(sequence="ATG")]}
    for requests, state in (
        ([wanted], "blocked"),
        ([wanted], "blocked"),
        ([other], "blocked"),
        ([other], "achieved"),
    ):
        save_hypothesis(
            Hypothesis(
                goal="g",
                inputs=inputs,
                state=state,
                round=1,
                attempts=[Attempt(round=1, requests=requests)],
            )
        )  # The achieved one is not waiting.

    rows = await _endpoint("/api/requests")()
    assert [(r.request.name, len(r.blocked)) for r in rows] == [
        ("wanted", 2),
        ("other", 1),
    ]

    client = _Client()
    await _endpoint("/api/hypotheses/{hyp_id}/{signal}", "POST", client)(
        "h1", "tool_added"
    )
    assert client.signals == [("h1", "tool_added")]


def test_a_file_without_state_still_gets_a_status_from_what_it_holds(results_dir):
    inputs = {"seq": [Dna(sequence="ATG")]}
    old = save_hypothesis(
        Hypothesis(goal="g", inputs=inputs)
    )  # state is None, as before the loop.
    path = results_dir / "hypotheses" / f"{old.id}.json"
    assert _hypothesis_row(path).status == "building"


def test_a_file_from_before_the_loop_is_read_as_its_only_round(results_dir):
    """It kept its run at the top level, not under ``attempts``."""
    path = results_dir / "hypotheses" / "old.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "old",
                "goal": "g",
                "inputs": {"seq": [{"kind": "dna", "sequence": "ATG"}]},
                "hypothesis": "the builder's plan, as it was saved",
                "verdict": {"achieved": True, "reason": "r"},
            }
        )
    )
    row = _hypothesis_row(path)
    cur = row.hypothesis.current
    assert row.status == "achieved" and cur and cur.verdict and cur.verdict.achieved


def test_reading_an_old_file_leaves_the_dict_it_was_given_alone():
    raw = {
        "goal": "g",
        "inputs": {"seq": [{"kind": "dna", "sequence": "ATG"}]},
        "verdict": {"achieved": True, "reason": "r"},
    }
    before = copy.deepcopy(raw)
    hyp = Hypothesis.model_validate(raw)
    assert raw == before and [a.round for a in hyp.attempts] == [1]
    # Read again, as the file the loop now writes: its rounds are not made over.
    assert (
        Hypothesis.model_validate(hyp.model_dump(mode="json")).attempts == hyp.attempts
    )


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
