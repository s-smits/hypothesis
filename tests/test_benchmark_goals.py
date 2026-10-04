"""The loop's goal built from a benchmark instance: the gate's rules, in words, with no answer."""

import json

from click.testing import CliRunner
from test_benchmark import WEIGHTS, inst

from node_dag import entrez
from node_dag.agent import Hypothesis
from node_dag.benchmark import Instance, exact_cai
from node_dag.types import Dna
from temporal.run_benchmark import goal_for, main

TGA = "ATGCTGAAAGGCTTTTGA"  # ends in TGA, which a most-frequent table would respell TAA


def claims(goal: dict) -> dict[str, str]:
    return {c["id"]: c["claim"] for c in goal["criteria"]}


def test_the_goal_is_a_loadable_hypothesis_for_the_instance():
    goal = goal_for(inst(TGA), WEIGHTS)
    hyp = Hypothesis.model_validate(goal)
    assert hyp.inputs["seqs"] == [Dna(sequence=TGA)]
    assert "codon adaptation" in hyp.goal
    assert json.dumps(dict(sorted(WEIGHTS.items())), separators=(",", ":")) in hyp.goal


def test_the_default_fixed_codons_are_named_with_their_codons():
    keep = claims(goal_for(inst(TGA), WEIGHTS))["fixed_codons_kept"]
    assert "ATG at index 0" in keep and "TGA at index 5" in keep


def test_an_explicit_immutable_set_replaces_the_default_in_the_goal():
    explicit = Instance(key="g", parent=Dna(sequence=TGA), immutable=frozenset({1}))
    goal = goal_for(explicit, WEIGHTS)
    assert "CTG at index 1" in claims(goal)["fixed_codons_kept"]
    assert "index 0" not in goal["goal"] and "index 5" not in goal["goal"]


def test_the_goal_writes_no_answer():
    instance = inst(TGA)
    text = json.dumps(goal_for(instance, WEIGHTS)).lower()
    assert exact_cai(instance, WEIGHTS).lower() not in text
    assert not {"optimum", "gap", "holdout", "held-out"} & set(text.split())


def fake_cds(n: int) -> list[dict]:
    """``n`` usable records, each a distinct short CDS."""
    stems = ["CTG", "AAA", "GGC", "TTT", "CCG", "GAA", "AGC", "GTT"]
    return [
        {
            "id": f"r{k}",
            "gene": f"g{k}",
            "sequence": "ATG" + stems[k % 8] * (2 + k % 3) + stems[(k + 3) % 8] + "TAA",
            "usable": True,
        }
        for k in range(n)
    ]


def test_emit_goals_writes_one_file_per_instance_and_records_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: fake_cds(12))
    out, ledger = tmp_path / "goals", tmp_path / "ledger"
    result = CliRunner().invoke(
        main,
        ["--instances", "4", "--holdout-fraction", "0.0", "--emit-goals", str(out)]
        + ["--ledger", str(ledger)],
    )
    assert result.exit_code == 0, result.output
    files = sorted(out.glob("goal_*.json"))
    assert len(files) == 4
    for f in files:
        goal = json.loads(f.read_text())
        assert Hypothesis.model_validate(goal).criteria[-1].id == "fixed_codons_kept"
    assert not ledger.exists()
