"""Goals generated from benchmark instances, and the export's refusals.

A generated goal has to ask what the gate scores, and must not carry the answer. The
export has to refuse rather than guess: a key that is not a safe filename, two instances
sharing one, and a directory that already holds goals are all errors, because
overwriting a goal silently would change what an earlier attempt was asked.
"""

import json

import pytest
from click.exceptions import ClickException

from node_dag.benchmark import (
    Instance,
    Ledger,
    cai_weights_from,
    goal_for,
    release_holdout,
    split_instances,
)
from node_dag.dna import codons
from node_dag.types import Dna
from temporal.run_benchmark import SAFE_KEY, write_goals

SEQ = "ATGGCTCTGAAAGGCCTGTTTCCGCTGTAA"
WEIGHTS = cai_weights_from([SEQ, "ATGAAAGGCTAA", "ATGCTGCCGTTTTAA"])


def inst(key: str = "gene1", sequence: str = SEQ) -> Instance:
    return Instance(key=key, parent=Dna(sequence=sequence))


# --- what a goal says --------------------------------------------------------


def test_a_goal_is_a_hypothesis_without_an_id():
    g = goal_for(inst(), WEIGHTS)
    assert set(g) == {"goal", "inputs", "criteria"}
    assert "id" not in g


def test_the_input_is_the_instance_sequence():
    g = goal_for(inst(), WEIGHTS)
    assert g["inputs"] == {"seqs": [{"kind": "dna", "sequence": SEQ}]}


def test_the_goal_carries_the_weight_table():
    g = goal_for(inst(), WEIGHTS)
    table = json.dumps(dict(sorted(WEIGHTS.items())), separators=(",", ":"))
    assert table in g["goal"]


def test_the_goal_names_the_fixed_codons_and_how_to_check_them():
    """The omission this fixes: a hand-written goal left the fixed codons out."""
    i = inst()
    cs = codons(SEQ)
    fixed = sorted(i.fixed())
    g = goal_for(i, WEIGHTS)
    assert f"immutable={fixed}" in g["goal"]
    assert "immutable_unchanged at 1" in g["goal"]
    for idx in fixed:
        assert f"{cs[idx]} at index {idx}" in g["goal"]


def test_the_criteria_cover_the_gates_and_the_objective():
    ids = [c["id"] for c in goal_for(inst(), WEIGHTS)["criteria"]]
    assert ids == [
        "protein_preserved",
        "higher_cai",
        "only_improved_kept",
        "length_preserved",
        "fixed_codons_kept",
    ]


def test_every_criterion_is_a_human_source_claim():
    for c in goal_for(inst(), WEIGHTS)["criteria"]:
        assert c["source"] == "human"
        assert c["claim"].strip()


def test_a_goal_never_carries_the_answer():
    """An optimum, a gap or a split in the goal would be worthless or leaking."""
    text = json.dumps(goal_for(inst(), WEIGHTS)).lower()
    for leak in ("optimum", "gap_closed", "exact_cai", "holdout", "held-out", "split"):
        assert leak not in text, leak


def test_explicit_immutable_indices_are_used():
    i = Instance(key="g", parent=Dna(sequence=SEQ), immutable=frozenset({2, 5}))
    g = goal_for(i, WEIGHTS)
    assert "immutable=[2, 5]" in g["goal"]


def test_the_goal_is_deterministic():
    assert goal_for(inst(), WEIGHTS) == goal_for(inst(), WEIGHTS)


def test_the_goal_is_json_serialisable():
    json.dumps(goal_for(inst(), WEIGHTS))


# --- the export --------------------------------------------------------------


def test_one_file_per_instance_named_by_key(tmp_path):
    instances = [inst("alpha"), inst("beta")]
    paths = write_goals(instances, WEIGHTS, tmp_path)
    assert [p.name for p in paths] == ["alpha.json", "beta.json"]
    assert all(p.exists() for p in paths)


def test_each_file_holds_that_instance_goal(tmp_path):
    other = "ATGAAAGGCTAA"
    instances = [inst("alpha"), inst("beta", other)]
    write_goals(instances, WEIGHTS, tmp_path)
    written = json.loads((tmp_path / "beta.json").read_text())
    assert written == goal_for(inst("beta", other), WEIGHTS)


def test_an_existing_goal_file_is_never_overwritten(tmp_path):
    write_goals([inst("alpha")], WEIGHTS, tmp_path)
    before = (tmp_path / "alpha.json").read_bytes()
    with pytest.raises(ClickException, match="already exist"):
        write_goals([inst("alpha")], WEIGHTS, tmp_path)
    assert (tmp_path / "alpha.json").read_bytes() == before


def test_nothing_is_written_when_one_file_already_exists(tmp_path):
    """The check runs before any write, so an export is all or nothing."""
    write_goals([inst("alpha")], WEIGHTS, tmp_path)
    with pytest.raises(ClickException, match="already exist"):
        write_goals([inst("alpha"), inst("fresh")], WEIGHTS, tmp_path)
    assert not (tmp_path / "fresh.json").exists()


@pytest.mark.parametrize(
    "key", ["../escape", "a/b", "with space", ".hidden", "", "key:colon", "-leading"]
)
def test_an_unsafe_key_is_refused(tmp_path, key: str):
    with pytest.raises(ClickException, match="safe file name"):
        write_goals([inst(key)], WEIGHTS, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", ["gene1", "b0001", "yfbL", "a.b", "a-b", "a_b", "A1"])
def test_a_safe_key_is_accepted(key: str):
    assert SAFE_KEY.match(key)


def test_duplicate_keys_are_refused(tmp_path):
    with pytest.raises(ClickException, match="share the key"):
        write_goals([inst("same"), inst("same", "ATGAAAGGCTAA")], WEIGHTS, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_empty_instance_list_writes_nothing(tmp_path):
    assert write_goals([], WEIGHTS, tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_the_directory_is_made_if_missing(tmp_path):
    out = tmp_path / "nested" / "goals"
    write_goals([inst("alpha")], WEIGHTS, out)
    assert (out / "alpha.json").exists()


# --- the export and the split ------------------------------------------------


def many(n: int) -> list[Instance]:
    return [
        Instance(
            key=f"gene{i}", parent=Dna(sequence="ATG" + "AAA" * (i % 4 + 1) + "TAA")
        )
        for i in range(n)
    ]


def test_exporting_development_goals_writes_no_ledger_entry(tmp_path):
    """An export is not an attempt, so nothing is recorded for the dev side."""
    led = Ledger(tmp_path / "ledger")
    split = split_instances(many(20), holdout_fraction=0.3)
    write_goals(list(split.dev), WEIGHTS, tmp_path / "goals")
    assert list(led.entries()) == []


def test_a_held_out_export_records_the_access_before_writing(tmp_path):
    led = Ledger(tmp_path / "ledger")
    split = split_instances(many(20), holdout_fraction=0.3)
    released = release_holdout(
        split,
        reason="frozen confirmation",
        frozen={"strategy": "loop"},
        ledger=led,
    )
    write_goals(list(released), WEIGHTS, tmp_path / "goals")
    accesses = led.holdout_accesses()
    assert len(accesses) == 1
    recorded = accesses[0]["split"]
    assert isinstance(recorded, dict)
    assert recorded["split_hash"] == split.split_hash


def test_a_refused_release_exports_nothing_and_logs_nothing(tmp_path):
    led = Ledger(tmp_path / "ledger")
    split = split_instances(many(20), holdout_fraction=0.3)
    with pytest.raises(ValueError, match="reason"):
        release_holdout(split, reason="  ", frozen={"s": 1}, ledger=led)
    assert led.holdout_accesses() == []
    assert not (tmp_path / "goals").exists()


def test_dev_and_holdout_goals_are_different_instances(tmp_path):
    split = split_instances(many(20), holdout_fraction=0.3)
    dev = write_goals(list(split.dev), WEIGHTS, tmp_path / "dev")
    held = write_goals(list(split.holdout), WEIGHTS, tmp_path / "held")
    assert {p.stem for p in dev}.isdisjoint({p.stem for p in held})
