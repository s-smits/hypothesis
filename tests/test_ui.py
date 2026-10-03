"""The UI's pure helpers: status, the waterfall, and the tool-request ranking.

No ``TestClient`` and no new dependency. ``_hypothesis_row``, ``_waterfall`` and
``_request_rows`` read files and return models, so they are testable as plain functions,
which is how the repo already tests this page.

The load-bearing test here is :func:`test_an_old_file_with_no_state_still_resolves`: every
results file on disk predates ``Hypothesis.state``, and ``state=None`` must keep working.
"""

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from node_dag.plan import (
    Attempt,
    Criterion,
    Hypothesis,
    HypothesisState,
    ToolRequest,
    Verdict,
)
from node_dag.types import Dna
from temporal.store import hypotheses_dir, save_hypothesis, save_tool_request
from temporal.ui.app import (
    HYPOTHESES,
    HypothesisStatus,
    _hypothesis_row,
    _kinds,
    _request_rows,
    _stats,
    _waterfall,
)

STATUSES = get_args(HypothesisStatus)
DNA = Dna(sequence="ATGTCGTAA")
DAG = {
    "inputs": {"seq": "dna"},
    "steps": {
        "protein": {
            "config": {"name": "dna_to_protein"},
            "inputs": {"sequence": "seq"},
        }
    },
}


def _request(name: str, node: str = "tool") -> ToolRequest:
    return ToolRequest(
        name=name,
        node=node,
        purpose="do the thing no existing node does",
        category="generation",
        inputs={"sequence": "dna"},
        output="dna" if node == "tool" else None,
        forwards=None if node == "tool" else "sequence",
        why_needed="the goal needs a targeted recoder and nothing on the shelf is one",
        why_not_composable="mutate_synonymous picks codons at random, so it cannot target",
        example="sequence=ATGCTGTAA, targets=('CTG',) -> ATGTTGTAA",
    )


def _blocked(goal: str, names: list[str]) -> Hypothesis:
    """Save a hypothesis blocked on each of ``names`` and return it."""
    hyp = Hypothesis(
        goal=goal,
        inputs={"seq": DNA},
        state="blocked",
        round=1,
        pending=[_request(n) for n in names],
    )
    return save_hypothesis(hyp)


@pytest.mark.parametrize("status", STATUSES)
def test_every_status_resolves_through_a_row(status: str) -> None:
    """Whatever the workflow recorded, the row carries it back unchanged."""
    hyp = save_hypothesis(Hypothesis(goal="recode it", inputs={"seq": DNA}, state=status))
    row = _hypothesis_row(hypotheses_dir() / f"{hyp.id}.json")
    assert row.status == status


def test_the_status_literal_covers_every_workflow_state() -> None:
    """A state the Literal does not list would be dropped from the page's tally."""
    assert set(get_args(HypothesisState)) <= set(STATUSES)


def test_no_new_status_has_a_space() -> None:
    """``cls()`` turns a status into one CSS class, so a status is one word.

    ``not achieved`` is the one exception, and it predates the page: its single space is
    why ``cls()`` replaces spaces at all. Everything added since is a single word.
    """
    assert [s for s in STATUSES if " " in s] == ["not achieved"]


@pytest.mark.parametrize("status", STATUSES)
def test_every_status_has_a_css_rule(status: str) -> None:
    """An unstyled badge is invisible, so every status needs its own ``.s-*`` rule."""
    css = HYPOTHESES.read_text(encoding="utf-8")
    assert re.search(rf"\.s-{re.escape(status.replace(' ', '-'))}\b", css)


def test_a_recorded_state_beats_the_waterfall() -> None:
    """Nothing but ``state`` can say "blocked": no field combination implies it."""
    hyp = save_hypothesis(
        Hypothesis(
            goal="recode it",
            inputs={"seq": DNA},
            state="blocked",
            dag=DAG,
            verdict=Verdict(reason="round 1 missed", agrees=False),
        )
    )
    row = _hypothesis_row(hypotheses_dir() / f"{hyp.id}.json")
    assert row.status == "blocked"
    assert _waterfall(row.hypothesis, row.progress) == "not achieved"


def _write_old(name: str, **fields: object) -> Path:
    """Write a pre-``state`` Hypothesis file by hand, as the single pass wrote them."""
    path = hypotheses_dir() / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"id": name, "goal": "convert DNA to protein", "inputs": {"seq": DNA.model_dump()}}
    path.write_text(json.dumps(body | fields), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, "building"),
        ({"hypothesis": "translate it"}, "building"),
        ({"dag": DAG}, "running"),
        ({"dag": DAG, "outcome": {"values": {"seq": DNA.model_dump()}, "skipped": []}}, "verifying"),
        ({"verdict": {"reason": "the protein is right", "achieved": True}}, "achieved"),
        ({"verdict": {"reason": "wrong protein", "achieved": False}}, "not achieved"),
    ],
)
def test_an_old_file_with_no_state_still_resolves(
    fields: dict[str, object], expected: str
) -> None:
    """A file with no ``state``, ``attempts`` or ``criteria`` goes through the waterfall.

    Hard backward compatibility: these files are what is already on disk.
    """
    path = _write_old("hypothesis-old", **fields)
    row = _hypothesis_row(path)
    assert row.status == expected
    assert row.hypothesis.state is None
    assert row.hypothesis.attempts == []
    assert row.hypothesis.criteria == []


def test_an_old_verdict_still_reads_as_achieved() -> None:
    """The old model-supplied ``achieved`` is the field the page still reads."""
    row = _hypothesis_row(
        _write_old("hypothesis-old", verdict={"reason": "right", "achieved": True})
    )
    assert row.hypothesis.verdict is not None
    assert row.hypothesis.verdict.achieved
    assert row.hypothesis.verdict.score == 0.0
    assert not row.hypothesis.verdict.prediction_held


def test_requests_rank_by_how_much_work_is_stuck() -> None:
    """Two hypotheses blocked on a tool outrank one, and distinct goals are counted."""
    save_tool_request("spare_tool", _request("spare_tool").model_dump_json().encode())
    _blocked("remove every CTG codon", ["recode_codons"])
    _blocked("remove every TCG codon", ["recode_codons", "count_rbs"])
    _blocked("remove every TCG codon", ["count_rbs", "lone_tool"])
    rows = {r.request.name: r for r in _request_rows()}
    # Both block two hypotheses, so the one spanning two goals breaks the tie, and the
    # contract nothing is waiting on comes last.
    assert [r.request.name for r in _request_rows()] == [
        "recode_codons",
        "count_rbs",
        "lone_tool",
        "spare_tool",
    ]
    assert len(rows["lone_tool"].blocked) == 1
    assert len(rows["count_rbs"].blocked) == 2
    assert rows["count_rbs"].goals == 1  # Both are the same goal.
    assert len(rows["recode_codons"].blocked) == 2
    assert rows["recode_codons"].goals == 2
    assert rows["spare_tool"].blocked == []


def test_a_contract_with_nobody_blocked_is_still_listed() -> None:
    """The file holds only the contract; who is waiting is computed every time."""
    save_tool_request("recode_codons", _request("recode_codons").model_dump_json().encode())
    (row,) = _request_rows()
    assert row.request.name == "recode_codons"
    assert row.blocked == []
    assert row.goals == 0
    assert row.satisfied  # recode_codons is registered, so it needs no more work.


def test_satisfied_says_whether_this_process_can_build_it() -> None:
    """``satisfied`` is a registry read in *this* process, not a stored flag."""
    _blocked("remove every CTG codon", ["dna_to_protein", "count_rbs"])
    rows = {r.request.name: r for r in _request_rows()}
    assert rows["dna_to_protein"].satisfied  # Registered in factory.MAPPING.
    assert not rows["count_rbs"].satisfied


def test_kinds_come_from_the_type_registry() -> None:
    """The form builds its dropdown from this, so it cannot offer a deleted kind."""
    kinds = {k.kind: k.field for k in _kinds()}
    assert kinds == {"amino_acid_sequence": "sequence", "dna": "sequence", "score": "value"}


def test_stats_split_a_tool_gap_from_a_reasoning_gap() -> None:
    """A critique from a round that asked for a node counts as a tool gap, not reasoning."""
    critique = {
        "diagnosis": "the plan wired the random mutator instead of a targeted recoder",
        "root_cause": "missing_tool",
        "evidence": ["recoded"],
        "fix": "ask for a node that targets the codons named in the goal",
        "reason": "no existing node can target a codon, so nothing else explains it",
    }
    reasoning = critique | {"root_cause": "goal_misread"}
    save_hypothesis(
        Hypothesis(
            goal="remove every CTG codon",
            criteria=[Criterion(id="no_ctg", claim="no CTG codon remains")],
            inputs={"seq": DNA},
            state="blocked",
            usage={"plan": 1200},
            pending=[_request("count_rbs")],
            attempts=[
                Attempt(
                    round=1,
                    requests=[_request("recode_codons")],
                    critique=critique,
                    started="2026-01-01T00:00:00Z",
                ),
                Attempt(round=2, critique=reasoning, started="2026-01-01T01:00:00Z"),
            ],
        )
    )
    stats = _stats()
    assert (stats.hypotheses, stats.attempts, stats.critiques) == (1, 2, 2)
    assert stats.tool_gap == 1
    assert stats.reasoning_gap == 1
    assert stats.blocked == 1
    assert stats.usage == {"plan": 1200}
    assert {r.root_cause: r.total for r in stats.by_root_cause} == {
        "goal_misread": 1,
        "missing_tool": 1,
    }
    assert (stats.requests, stats.unsatisfied) == (1, 1)
