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
from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from node_dag import amass
from node_dag.agent import Hypothesis
from node_dag.dag import Dag, DagProgress
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.plan import Attempt, Criterion, Observation, ToolRequest
from node_dag.registry import Registry
from node_dag.types import Dna, Rna
from temporal.dag.activities import SavedRun, SaveWorkflowInput, results_subdir
from temporal.hypothesis.activities import save_hypothesis
from temporal.hypothesis.loop import HypothesisInput
from temporal.ui.app import (
    NewCriterion,
    NewDraft,
    NewHypothesis,
    _hypothesis_row,
    make_app,
)

UI = Path(__file__).parent.parent / "temporal" / "ui"
NEW, HYPOTHESES = UI / "new.html", UI / "hypotheses.html"
CSS = UI / "ui.css"
PAGES = (UI / "index.html", HYPOTHESES, NEW, UI / "nodes.html")


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


async def test_every_page_draws_from_one_stylesheet():
    """One look, in one file: a page's own <style> adds to it, not a copy of it."""
    served = await _endpoint("/ui.css")()
    assert isinstance(served, FileResponse)
    assert Path(served.path) == CSS
    tokens = CSS.read_text()
    for name in ("--bg:", "--running:", ".badge", "details.sec"):
        assert name in tokens
    for page in PAGES:
        text = page.read_text()
        assert text.count('<link rel="stylesheet" href="/ui.css">') == 1
        # The palette is defined once, so the pages cannot drift apart.
        assert "--bg:" not in text


def test_the_hypotheses_page_opens_a_goal_and_a_hypothesis_in_place():
    """One page: goals, their hypotheses and statuses, and detail behind a toggle."""
    page = HYPOTHESES.read_text()
    assert '<details class="goal"' in page
    assert '<details class="hyp"' in page
    for label in ("Success criteria", "Rounds", "Observations", "DAG", "Outcome"):
        assert f'"{label}"' in page
    # Opening something must survive the poll's redraw.
    assert "open.add" in page and "open.has" in page
    # No drill-down: nothing navigates away to a goal or hypothesis of its own.
    assert "crumbs" not in page
    assert "pushState" not in page


def test_a_blocked_run_still_offers_resume_and_abandon_on_the_one_page():
    """The loop's own controls survive the move to sections; they are not folded away."""
    page = HYPOTHESES.read_text()
    assert "blockedView" in page and "requestView" in page
    for s in ('button("tool_added", "Resume")', 'button("abandon", "Abandon")'):
        assert s in page
    assert 'data-signal="${signal}"' in page  # What those two buttons carry.
    assert "/${b.dataset.signal}" in page  # Posted back to signal the workflow.
    # blockedView is called directly, not through sec(), so a blocked run reads at a glance.
    assert "${blockedView(h)}" in page
    assert "sec(`${key}:blocked" not in page


def test_the_hypothesis_view_drops_the_inputs_and_the_observations_page():
    """The agent's inputs are plumbing; the record stays, reachable where it is cited."""
    page = HYPOTHESES.read_text()
    assert "inputChips" not in page
    assert "input_sources" not in page
    for other in PAGES:
        assert 'href="/observations' not in other.read_text()
    with pytest.raises(StopIteration):
        _endpoint("/observations")
    # The record itself is still read, in the hypothesis that cites it.
    assert "/api/observations/" in page
    assert "loadRecord" in page


async def test_starting_without_max_rounds_is_accepted_and_blank_criteria_are_dropped():
    client = _Client()
    new = NewHypothesis(
        goal="g",
        inputs={"seq": [Dna(sequence="ATG")]},
        criteria=[
            NewCriterion(kind="quantitative", text="no TCG remains"),
            NewCriterion(kind="qualitative", text="  "),
        ],
        max_rounds=None,
    )
    hyp = await _endpoint("/api/hypotheses", "POST", client, "model")(new)
    ((inp, kw),) = client.started
    assert (
        inp.max_rounds == 3 and kw["id"] == hyp.id
    )  # Blank means the default, not a 422.
    assert [c.claim for c in inp.hypothesis.criteria] == ["no TCG remains"]
    assert [c.kind for c in inp.hypothesis.criteria] == ["quantitative"]
    assert [c.source for c in inp.hypothesis.criteria] == [
        "human"
    ]  # Typed by a person.
    # Saved before the workflow starts, so the page has it with no worker running.
    saved = results_subdir("hypotheses") / f"{hyp.id}.json"
    assert Hypothesis.model_validate_json(saved.read_bytes()).state == "building"


async def test_a_goal_alone_starts_and_the_loop_fetches_its_inputs():
    client = _Client()
    hyp = await _endpoint("/api/hypotheses", "POST", client, "model")(
        NewHypothesis(goal="translate the E. coli lacZ CDS")
    )
    ((inp, _),) = client.started
    assert hyp.inputs == {} and inp.hypothesis.inputs == {}
    assert (
        inp.build_model == "model" and inp.verify_model != "model"
    )  # Not its own judge.


async def test_inputs_that_cannot_run_are_a_422_and_nothing_is_saved_or_started():
    """An empty list, or a mix of kinds, is the caller's mistake, not a 500."""
    client = _Client()
    start = _endpoint("/api/hypotheses", "POST", client, "model")
    for bad in (
        {"seq": []},
        {"seq": [Dna(sequence="ATG"), Rna(sequence="AUG")]},
    ):
        with pytest.raises(HTTPException) as e:
            await start(NewHypothesis(goal="g", inputs=bad))
        assert e.value.status_code == 422 and "seq" in e.value.detail
    assert client.started == []
    assert not list(results_subdir("hypotheses").glob("*.json"))
    with pytest.raises(ValidationError):  # Blank after trimming: no goal at all.
        NewHypothesis(goal="  \n")


async def test_a_workflow_that_will_not_start_leaves_a_failed_file_and_a_503():
    """The Hypothesis is saved before the start; a failed start must not leave it building."""

    class Down(_Client):
        async def start_workflow(self, run, inp: HypothesisInput, **kw) -> None:
            raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")

    with pytest.raises(HTTPException) as e:
        await _endpoint("/api/hypotheses", "POST", Down(), "model")(
            NewHypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]})
        )
    assert e.value.status_code == 503 and "connection refused" in e.value.detail
    (saved,) = results_subdir("hypotheses").glob("*.json")
    hyp = Hypothesis.model_validate_json(saved.read_bytes())
    assert hyp.state == "failed" and "connection refused" in (hyp.stopped_because or "")


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

    stale = results_subdir("requests") / "stale.json"
    stale.write_text('{"name": "stale"}')  # No longer validates: skipped, not a 500.
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


async def test_the_runs_page_shows_what_a_reference_filter_compares_with():
    """beats_reference has no threshold: it is scored in another step, which is an edge."""
    page = await _endpoint("/")()
    index = (Path(page.path).parent / "index.html").read_text()
    assert (
        "args.reference" in index
    )  # The node panel names the reference, not "vs undefined".
    assert "args.scored_in" in index
    assert (
        "step.config.scored_in" in index
    )  # The graph draws an edge from the scoring step.
    assert (
        "Object.values(p.dag.steps[k].inputs)" not in index
    )  # No second, input-only reading.


async def test_a_new_hypothesis_keeps_the_kind_and_text_of_the_criteria_the_user_sent():
    client = _Client()
    sent = [
        NewCriterion(kind="qualitative", text="the protein is unchanged"),
        NewCriterion(kind="quantitative", text="expression above the input's"),
    ]
    await _endpoint("/api/hypotheses", "POST", client, "model")(
        NewHypothesis(goal="g", criteria=sent)
    )
    ((inp, _),) = client.started
    got = inp.hypothesis.criteria
    assert [(c.id, c.kind, c.claim, c.source) for c in got] == [
        ("c1", "qualitative", "the protein is unchanged", "human"),
        ("c2", "quantitative", "expression above the input's", "human"),
    ]


async def test_the_observations_endpoint_searches_the_literature_for_a_goal():
    """The page asks for this before a run, so the user can edit what it found."""
    seen: list[ModelMessage] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(messages)
        # Nothing cited, so nothing to check against a record: the empty answer is
        # the one an agent with no search behind it can give.
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"observations": []})]
        )

    search = _endpoint("/api/observations", "POST", None, FunctionModel(script))
    got = await search(NewDraft(goal="faster lacZ", hypothesis="mutate codons"))

    assert got == []
    prompt = next(p.content for p in seen[0].parts if isinstance(p, UserPromptPart))
    assert "Goal: faster lacZ" in str(prompt)
    assert "Proposed hypothesis: mutate codons" in str(prompt)

    with pytest.raises(HTTPException) as e:  # No model, no agent.
        await _endpoint("/api/observations", "POST")(NewDraft(goal="faster lacZ"))
    assert e.value.status_code == 503

    def down(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("no API key")

    broken = _endpoint("/api/observations", "POST", None, FunctionModel(down))
    with pytest.raises(HTTPException) as e:  # The page shows why, not a bare 500.
        await broken(NewDraft(goal="faster lacZ"))
    assert e.value.status_code == 502 and "no API key" in e.value.detail


async def test_a_new_hypothesis_keeps_the_literature_the_user_sent():
    client = _Client()
    kept = [
        Observation(
            amass_id="AMBC_1",
            summary="Codon usage sets expression.",
            core="biomedcore",
            title="Codon usage and expression",
        )
    ]
    await _endpoint("/api/hypotheses", "POST", client, "model")(
        NewHypothesis(goal="g", observations=kept)
    )
    ((inp, _),) = client.started
    assert inp.hypothesis.observations == kept


def test_the_new_page_edits_criteria_and_can_draft_them_with_the_agent():
    page = NEW.read_text()
    for s in ("Success criteria", "quantitative", "qualitative", "Add criterion"):
        assert s in page
    assert 'id="add-criterion"' in page
    assert "/api/criteria" in page  # The "draft with the agent" button's call.
    assert "criteria: criteria()" in page  # They are sent when the run starts.
    assert "max_rounds" in page  # The loop's own limit stays on the form.


def test_the_new_page_edits_the_observations_it_gathers_before_a_run():
    page = NEW.read_text()
    assert "Observations" in page
    assert "/api/observations" in page  # The "search the literature" button's call.
    assert "observations: observations()" in page  # Sent when the run starts.
    # A summary is editable, and the record it cites is not re-typed by hand.
    assert "observationRow" in page
    assert "...r.obs" in page


def test_the_hypotheses_page_marks_each_criterion_met_not_met_or_unclear():
    page = HYPOTHESES.read_text()
    assert "criterionLine" in page  # Pairs a criterion with its call.
    for s in ("met", "not met", "unclear"):
        assert s in page
    # The loop makes the call from the assertions that covered the criterion, as they held.
    assert "att.held" in page and "a.criterion === c.id" in page


def test_the_new_page_takes_words_and_the_builder_chooses_what_to_run_on():
    """The loop fetches the inputs the goal names, so the form does not ask for them."""
    page = NEW.read_text()
    assert 'id="inputs"' not in page and "readInputs" not in page
    assert "inputs:" not in page  # The request body carries no inputs.


async def test_the_runs_page_draws_a_beats_reference_filter_as_it_reads():
    page = await _endpoint("/")()
    index = (Path(page.path).parent / "index.html").read_text()
    # The filter also reads the step named in scored_in, so the graph draws that edge,
    # and its label names the reference, not a threshold it does not have.
    assert "config.scored_in" in index
    assert "scored in" in index


async def test_the_criteria_endpoint_drafts_a_list_to_edit():
    """The agent's draft comes back for the user to edit before they start a run."""
    seen: list[ModelMessage] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(messages)
        draft = [
            {"id": "faster", "claim": "twice the expression"},
            {"id": "same_protein", "claim": "the protein is unchanged"},
        ]
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"response": draft})]
        )

    draft = _endpoint("/api/criteria", "POST", None, FunctionModel(script))
    got = await draft(NewDraft(goal="faster lacZ", hypothesis="mutate codons"))

    assert got == [
        Criterion(id="faster", claim="twice the expression"),
        Criterion(id="same_protein", claim="the protein is unchanged"),
    ]
    prompt = next(p.content for p in seen[0].parts if isinstance(p, UserPromptPart))
    assert "Goal: faster lacZ" in str(prompt)
    assert "Proposed hypothesis: mutate codons" in str(prompt)

    with pytest.raises(HTTPException) as e:  # No model, no agent.
        await _endpoint("/api/criteria", "POST")(NewDraft(goal="faster lacZ"))
    assert e.value.status_code == 503

    def down(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("no API key")

    broken = _endpoint("/api/criteria", "POST", None, FunctionModel(down))
    with pytest.raises(HTTPException) as e:  # The page shows why, not a bare 500.
        await broken(NewDraft(goal="faster lacZ"))
    assert e.value.status_code == 502 and "no API key" in e.value.detail


def test_a_blank_goal_is_refused_before_it_can_reach_the_criteria_agent():
    with pytest.raises(ValidationError):  # Blank after trimming: no goal at all.
        NewDraft(goal="  \n")
    assert NewDraft(goal="  faster lacZ \n").goal == "faster lacZ"
