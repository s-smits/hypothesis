"""Saved loop runs scored through the harness. No model: a tiny Hypothesis is built."""

import json

import click
import pytest
from test_benchmark import PAIRS, WEIGHTS, inst, three_instances

from node_dag.agent import Hypothesis
from node_dag.benchmark import (
    Ledger,
    cai_objective,
    codon_pair_objective,
    gate,
    manifest,
    record_attempt,
    run_strategy,
)
from node_dag.dag import DagOutput
from node_dag.plan import Attempt, Verdict
from node_dag.types import Dna, Table
from temporal.run_benchmark import loop_line, loop_runs

G = {  # Four genes: one kept, one abandoned, one kept nothing, one with another goal.
    "arfA": "ATGCTGAAAGGCTTTTAA",
    "abandoned": "ATGGGCTTTAAACTGTAA",
    "empty": "ATGAAAAAACTGGGCTAA",
    "other": "ATGTTTGGCGGCAAATAA",
}
INSTANCES = [inst(s, k) for k, s in G.items()]
OBJ = cai_objective(WEIGHTS)
MID = "ATGTTAAAAGGCTTTTAA"  # arfA, codon 1 a synonym: passes, below the optimum.
BAD = "ATGGTAAAAGGCTTTTAA"  # arfA, codon 1 Leu to Val: scores above MID, wrong protein.


def save(d, gene, *, kept=(), ok=True, n=1, tokens=1, goal="codon adaptation"):
    table = Table.of([Dna(sequence=s) for s in kept])
    last = Attempt(
        round=n,
        verdict=Verdict(achieved=ok, reason="r"),
        outcome=DagOutput(values={"keep.yes": table}, skipped=[]),
    )
    hyp = Hypothesis(
        goal=f"Raise the {goal} of the sequence",
        inputs={"seq": [Dna(sequence=G[gene])]},
        attempts=[*(Attempt(round=r) for r in range(1, n)), last],
        usage={"total": tokens},
    )
    (d / "hypotheses").mkdir(parents=True, exist_ok=True)
    (d / "hypotheses" / f"{hyp.id}.json").write_text(hyp.model_dump_json())


@pytest.fixture
def runs(tmp_path):
    d = tmp_path / "runs"
    save(d, "arfA", kept=[G["arfA"], MID, BAD], n=3, tokens=500)
    save(d, "abandoned", ok=False, n=3, tokens=400)
    save(d, "empty", tokens=100)
    save(d, "other", goal="translation initiation rate", tokens=9999)
    (d / "hypotheses" / "broken.json").write_text("{not json")
    return d


def scored(runs):
    propose, budget, notes, selection = loop_runs(runs, INSTANCES, "codon adaptation")
    return run_strategy(INSTANCES, OBJ, "loop", propose), budget, notes, selection


def test_budget_is_recorded_when_given_and_absent_otherwise(tmp_path):
    insts, obj = three_instances(), codon_pair_objective(PAIRS)
    out = {n: run_strategy(insts, obj, n, lambda i: []) for n in ("fixed", "loop")}
    led, m = Ledger(tmp_path / "ledger"), manifest(insts, obj, PAIRS, seed=1)

    def rec(**kw):
        path = record_attempt(led, manifest_=m, split=None, results=out, **kw)
        return json.loads(path.read_text())["strategies"]

    got = rec(budget={"loop": {"rounds": 3, "tokens": 500}})
    assert got["loop"]["budget"] == {"rounds": 3, "tokens": 500}
    assert "budget" not in got["fixed"]
    assert all("budget" not in s for s in rec().values())


def test_best_passing_kept_candidate_scores_and_a_changed_protein_is_gated(runs):
    rows, _, _, selection = scored(runs)
    assert gate(INSTANCES[0], BAD)["protein_unchanged"] == 0.0
    assert OBJ.score(BAD) > OBJ.score(MID)  # Ungated, it would have been chosen.
    assert (rows[0].candidate, rows[0].evaluations, rows[0].passed) == (MID, 3, True)
    assert "no answer step" in selection and "'arfA': 3" in selection


def test_abandoned_empty_and_other_goal_genes_are_failed_rows_not_dropped(runs):
    rows, _, _, _ = scored(runs)
    assert [r.instance for r in rows] == list(G)
    assert [(r.passed, r.candidate, r.evaluations) for r in rows[1:]] == [
        (False, None, 0)
    ] * 3


def test_budget_and_both_means_come_from_the_runs_that_were_scored(runs):
    rows, budget, notes, _ = scored(runs)
    assert budget == {"rounds": 7, "tokens": 1000}  # Abandoned counts; other goal, no.
    assert len(notes) == 2 and "broken.json" in notes[0]
    gap = rows[0].gap_closed
    want = f"{gap / 4:.1%} over all 4 genes, {gap:.1%} over the 1 with a gap"
    assert want in loop_line(rows)


def test_two_runs_for_one_gene_are_refused(runs):
    save(runs, "arfA", kept=[G["arfA"]])
    with pytest.raises(click.ClickException, match="arfA"):
        loop_runs(runs, INSTANCES, "codon adaptation")
