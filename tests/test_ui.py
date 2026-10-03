import copy
import json
from pathlib import Path
from typing import Any, cast

from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from temporalio.client import Client

from node_dag.agent import Hypothesis
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.plan import Attempt, ToolRequest
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.dag.activities import results_subdir
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


async def test_nodes_api_lists_what_the_builder_registered(results_dir):
    assert await _endpoint("/api/nodes")() == []
    score = DnaAtomScoreConfig(reference=Dna(sequence="ATGGCTCTGAAATAA"))
    column = score.columns()["atom_count"]
    registry = Registry(results_subdir("registry"))
    registry.register(score, "atoms and protein changes")
    registry.register(AtMostConfig(column=column, threshold=400), "small ones")

    nodes = await _endpoint("/api/nodes")()

    scorer, filt = sorted(nodes, key=lambda n: n["config"]["name"] != "dna_atom_score")
    assert scorer["node"] == f"dna_atom_score__{score.config_hash}"
    assert scorer["description"] == "atoms and protein changes"
    assert scorer["categories"] == ["scoring"]
    assert scorer["score_columns"]["atom_count"] == column
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
