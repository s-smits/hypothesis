"""The deterministic strategy-comparison harness.

The optimisers claim to be exact, which is the claim the whole comparison rests on, so
they are checked against brute force over every allowed sequence rather than against
each other.
"""

import json
import math
import random
from itertools import product

import pytest

from node_dag.benchmark import (
    Instance,
    Ledger,
    cai_objective,
    cai_weights_from,
    codon_pair_objective,
    compare,
    default_immutable,
    dependency_versions,
    exact_cai,
    exact_codon_pair,
    gate,
    greedy_chain,
    manifest,
    mean_pair_weight,
    pair_count,
    pair_weights_from,
    per_instance,
    random_synonymous,
    record_attempt,
    release_holdout,
    run_strategy,
    split_instances,
)
from node_dag.dna import CODON_TABLE, codons
from node_dag.types import Dna

# Short enough to brute force: ATG, four mutable codons, TAA.
SHORT = "ATGCTGAAAGGCTTTTAA"
rng = random.Random(7)
SENSE = [c for c, aa in CODON_TABLE.items() if aa != "*"]
WEIGHTS = {c: round(rng.uniform(0.1, 1.0), 4) for c in CODON_TABLE}
PAIRS = {a + b: round(rng.uniform(0.0, 1.0), 4) for a in SENSE for b in SENSE}


def inst(sequence: str = SHORT, key: str = "g1") -> Instance:
    return Instance(key=key, parent=Dna(sequence=sequence))


def all_allowed(instance: Instance) -> list[str]:
    """Every sequence the instance's choices allow. Only for short sequences."""
    return ["".join(p) for p in product(*instance.choices())]


# --- choices and immutability ------------------------------------------------


def test_start_and_stop_are_immutable_by_default():
    i = inst()
    assert default_immutable(SHORT) == frozenset({0, 5})
    assert i.choices()[0] == ("ATG",)
    assert i.choices()[-1] == ("TAA",)


def test_every_candidate_keeps_the_start_and_stop_codon():
    i = inst()
    for seq in all_allowed(i):
        assert seq.startswith("ATG")
        assert seq.endswith("TAA")


def test_explicit_immutable_positions_are_honoured():
    i = Instance(key="g", parent=Dna(sequence=SHORT), immutable=frozenset({1}))
    assert i.choices()[1] == ("CTG",)
    # Position 0 is now mutable, but methionine has no synonym anyway.
    assert i.choices()[0] == ("ATG",)


def test_a_codon_with_no_synonym_gives_one_choice():
    i = inst("ATGTGGAAATAA")  # TGG is tryptophan, unique
    assert i.choices()[1] == ("TGG",)


def test_choices_are_all_synonymous():
    i = inst()
    protein = "".join(CODON_TABLE[c] for c in codons(SHORT))
    for seq in all_allowed(i):
        assert "".join(CODON_TABLE[c] for c in codons(seq)) == protein


def test_empty_sequence_has_no_choices():
    assert Instance(key="g", parent=Dna(sequence="")).choices() == []
    assert default_immutable("") == frozenset()


# --- the optimisers are exact ------------------------------------------------


def test_exact_cai_beats_every_allowed_sequence():
    i = inst()
    obj = cai_objective(WEIGHTS)
    best = obj.score(exact_cai(i, WEIGHTS))
    for seq in all_allowed(i):
        assert obj.score(seq) <= best + 1e-12, seq


def test_exact_codon_pair_beats_every_allowed_sequence():
    i = inst()
    obj = codon_pair_objective(PAIRS)
    best = obj.score(exact_codon_pair(i, PAIRS))
    for seq in all_allowed(i):
        assert obj.score(seq) <= best + 1e-12, seq


def test_exact_codon_pair_matches_brute_force_exactly():
    i = inst()
    obj = codon_pair_objective(PAIRS)
    assert obj.score(exact_codon_pair(i, PAIRS)) == pytest.approx(
        max(obj.score(s) for s in all_allowed(i))
    )


def test_exact_optimisers_respect_immutable_positions():
    i = inst()
    for seq in (exact_cai(i, WEIGHTS), exact_codon_pair(i, PAIRS)):
        assert seq.startswith("ATG")
        assert seq.endswith("TAA")
        assert len(seq) == len(SHORT)


def test_exact_optimisers_preserve_the_protein():
    i = inst()
    protein = "".join(CODON_TABLE[c] for c in codons(SHORT))
    for seq in (exact_cai(i, WEIGHTS), exact_codon_pair(i, PAIRS)):
        assert "".join(CODON_TABLE[c] for c in codons(seq)) == protein


def test_optimisers_are_deterministic():
    i = inst()
    assert exact_cai(i, WEIGHTS) == exact_cai(i, WEIGHTS)
    assert exact_codon_pair(i, PAIRS) == exact_codon_pair(i, PAIRS)


def test_optimisers_handle_an_empty_sequence():
    i = Instance(key="g", parent=Dna(sequence=""))
    assert exact_codon_pair(i, PAIRS) == ""
    assert greedy_chain(i, PAIRS) == ""


# --- greedy is not exact, which is the point ---------------------------------


def test_greedy_never_beats_the_exact_optimum():
    obj = codon_pair_objective(PAIRS)
    for n in range(2, 9):
        seq = "ATG" + "".join(rng.choice(SENSE) for _ in range(n)) + "TAA"
        i = inst(seq, key=f"g{n}")
        assert (
            obj.score(greedy_chain(i, PAIRS))
            <= obj.score(exact_codon_pair(i, PAIRS)) + 1e-12
        )


def test_greedy_is_strictly_worse_somewhere():
    """If greedy always tied the optimum, the objective would need no search."""
    obj = codon_pair_objective(PAIRS)
    gaps = 0
    for n in range(2, 25):
        seq = "ATG" + "".join(rng.choice(SENSE) for _ in range(n)) + "TAA"
        i = inst(seq, key=f"g{n}")
        if (
            obj.score(exact_codon_pair(i, PAIRS)) - obj.score(greedy_chain(i, PAIRS))
            > 1e-9
        ):
            gaps += 1
    assert gaps > 0


def test_greedy_per_codon_is_exact_for_cai():
    """CAI is separable, so the myopic choice is the optimum. Checked, not assumed."""
    obj = cai_objective(WEIGHTS)
    i = inst()
    per_codon = "".join(
        max(choice, key=lambda c: (WEIGHTS.get(c, 0.0), c)) for choice in i.choices()
    )
    assert obj.score(per_codon) == pytest.approx(obj.score(exact_cai(i, WEIGHTS)))


# --- the node and the harness agree -----------------------------------------


def test_mean_pair_weight_matches_the_node():
    obj = codon_pair_objective(PAIRS)
    for seq in all_allowed(inst())[:40]:
        assert obj.score(seq) == pytest.approx(mean_pair_weight(seq, PAIRS))


def test_pair_count_counts_adjacent_pairs():
    assert pair_count(SHORT) == len(codons(SHORT)) - 1
    assert pair_count("") == 0
    assert pair_count("ATG") == 0


# --- random baseline ---------------------------------------------------------


def test_random_synonymous_is_synonymous_and_keeps_length():
    i = inst()
    protein = "".join(CODON_TABLE[c] for c in codons(SHORT))
    for draw in range(8):
        seq = random_synonymous(i, seed=1, draw=draw)
        assert len(seq) == len(SHORT)
        assert "".join(CODON_TABLE[c] for c in codons(seq)) == protein


def test_random_synonymous_is_seeded_per_instance_not_per_batch():
    a = Instance(key="a", parent=Dna(sequence=SHORT))
    b = Instance(key="b", parent=Dna(sequence=SHORT))
    assert random_synonymous(a, seed=1) == random_synonymous(a, seed=1)
    assert random_synonymous(a, seed=1) != random_synonymous(b, seed=1)


def test_different_draws_differ():
    i = inst()
    draws = {random_synonymous(i, seed=2, draw=d) for d in range(10)}
    assert len(draws) > 1


# --- gates -------------------------------------------------------------------


def test_gate_passes_a_synonymous_candidate():
    i = inst()
    row = gate(i, random_synonymous(i, seed=3))
    assert row["protein_unchanged"] == 1.0
    assert row["length_unchanged"] == 1.0
    assert row["immutable_unchanged"] == 1.0


def test_gate_catches_a_changed_protein():
    i = inst()
    broken = "ATGTGGAAAGGCTTTTAA"  # CTG -> TGG changes L to W
    assert gate(i, broken)["protein_unchanged"] == 0.0


def test_gate_catches_a_changed_length():
    i = inst()
    assert gate(i, SHORT[:-3])["length_unchanged"] == 0.0


def test_gate_catches_a_swapped_stop_codon():
    """The three stops translate alike, so only the immutable check can see this."""
    row = gate(inst(), SHORT[:-3] + "TAG")
    assert row["protein_unchanged"] == 1.0
    assert row["immutable_unchanged"] == 0.0


def test_gate_reads_the_instances_own_immutable_set():
    i = Instance(key="g", parent=Dna(sequence=SHORT), immutable=frozenset({1}))
    assert gate(i, "ATGTTAAAAGGCTTTTAA")["immutable_unchanged"] == 0.0  # CTG -> TTA
    assert gate(i, "ATGCTGAAGGGCTTTTAA")["immutable_unchanged"] == 1.0  # AAA -> AAG
    # The explicit set replaces the default, as in choices(): the stop is free here.
    assert gate(i, SHORT[:-3] + "TAG")["immutable_unchanged"] == 1.0


# --- running strategies ------------------------------------------------------


def three_instances() -> list[Instance]:
    return [
        Instance(key="a", parent=Dna(sequence="ATGCTGAAAGGCTTTTAA")),
        Instance(key="b", parent=Dna(sequence="ATGGCTCCGTTAGGCTAA")),
        Instance(key="c", parent=Dna(sequence="ATGTTTCCGCTGAAATAA")),
    ]


def strategies(objective_pairs: dict[str, float]):
    return {
        "random": lambda i: [random_synonymous(i, seed=11)],
        "best_of_8": lambda i: [
            random_synonymous(i, seed=11, draw=d) for d in range(8)
        ],
        "greedy": lambda i: [greedy_chain(i, objective_pairs)],
        "exact": lambda i: [exact_codon_pair(i, objective_pairs)],
    }


def test_one_row_per_instance_per_strategy():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    out = compare(insts, obj, strategies(PAIRS))
    for name, rows in out.items():
        assert [r.instance for r in rows] == [i.key for i in insts], name


def test_the_expected_ordering_holds_on_average():
    """random <= best_of_8 <= exact, and greedy <= exact."""
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    out = compare(insts, obj, strategies(PAIRS))
    mean = {
        k: sum(r.score for r in rows if r.score is not None) / len(rows)
        for k, rows in out.items()
    }
    assert mean["random"] <= mean["best_of_8"] + 1e-12
    assert mean["best_of_8"] <= mean["exact"] + 1e-12
    assert mean["greedy"] <= mean["exact"] + 1e-12


def test_exact_reaches_the_full_optimum_on_every_instance():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    rows = run_strategy(insts, obj, "exact", lambda i: [exact_codon_pair(i, PAIRS)])
    for r in rows:
        assert r.fraction_of_optimum == pytest.approx(1.0)


def test_a_strategy_never_exceeds_the_optimum():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    for rows in compare(insts, obj, strategies(PAIRS)).values():
        for r in rows:
            f = r.fraction_of_optimum
            assert f is None or f <= 1.0 + 1e-12


def test_evaluations_records_the_budget():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    out = compare(insts, obj, strategies(PAIRS))
    assert all(r.evaluations == 1 for r in out["random"])
    assert all(r.evaluations == 8 for r in out["best_of_8"])


def test_a_strategy_producing_nothing_stays_in_the_denominator():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    rows = run_strategy(insts, obj, "barren", lambda i: [])
    assert len(rows) == len(insts)
    for r in rows:
        assert r.candidate is None
        assert r.score is None
        assert r.gates == {}
        assert r.passed is False
        assert r.fraction_of_optimum is None


def test_a_protein_breaking_proposal_is_not_selected():
    insts = [three_instances()[0]]
    obj = codon_pair_objective(PAIRS)
    broken = "ATGTGGAAAGGCTTTTAA"
    rows = run_strategy(insts, obj, "broken", lambda i: [broken])
    assert rows[0].candidate is None
    assert rows[0].passed is False


def test_a_mixed_proposal_list_keeps_only_the_valid_best():
    insts = [three_instances()[0]]
    obj = codon_pair_objective(PAIRS)
    broken = "ATGTGGAAAGGCTTTTAA"
    good = exact_codon_pair(insts[0], PAIRS)
    rows = run_strategy(insts, obj, "mixed", lambda i: [broken, good])
    assert rows[0].candidate == good
    assert rows[0].evaluations == 2  # the broken one still cost an evaluation


def test_a_stop_swap_cannot_win_a_row_or_push_gap_closed_past_one():
    i = three_instances()[0]
    best = exact_codon_pair(i, PAIRS)
    swapped = best[:-3] + "TAG"
    pairs = {**PAIRS, best[-6:-3] + "TAG": 5.0}  # a stop pair worth more than any other
    obj = codon_pair_objective(pairs)
    assert obj.score(swapped) > obj.score(best)  # the hole pays on this objective
    (only,) = run_strategy([i], obj, "swap", lambda x: [swapped])
    assert only.candidate is None and only.passed is False
    (mixed,) = run_strategy([i], obj, "mixed", lambda x: [best, swapped])
    assert mixed.candidate == best
    assert mixed.gap_closed == pytest.approx(1.0)


@pytest.mark.filterwarnings("ignore:Partial codon")
def test_a_candidate_that_breaks_the_length_does_not_beat_a_passing_one():
    i = three_instances()[0]
    obj = codon_pair_objective(PAIRS)
    good = exact_codon_pair(i, PAIRS)
    # A trailing partial codon is ignored by the protein check and by the score, so
    # the two tie, and the longer string would win the tie without the length gate.
    (row,) = run_strategy([i], obj, "padded", lambda x: [good, good + "A"])
    assert row.candidate == good and row.passed


def test_fraction_is_none_when_the_optimum_is_not_positive():
    insts = [three_instances()[0]]
    zero = {k: 0.0 for k in PAIRS}
    obj = codon_pair_objective(zero)
    rows = run_strategy(insts, obj, "exact", lambda i: [exact_codon_pair(i, zero)])
    assert rows[0].optimum == 0.0
    assert rows[0].fraction_of_optimum is None


def test_per_instance_keeps_every_instance():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    rows = run_strategy(insts, obj, "exact", lambda i: [exact_codon_pair(i, PAIRS)])
    out = per_instance(rows, insts)
    assert len(out) == len(insts)
    assert {o.instance for o in out} == {i.key for i in insts}
    assert all(o.scored for o in out)


def test_per_instance_keeps_an_instance_with_no_candidate():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    rows = run_strategy(insts, obj, "barren", lambda i: [])
    out = per_instance(rows, insts)
    assert len(out) == len(insts)
    assert not any(o.scored for o in out)


# --- manifest ----------------------------------------------------------------


def test_manifest_records_what_a_rerun_needs():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    m = manifest(insts, obj, PAIRS, seed=11, notes="smoke")
    assert m["objective"] == "codon_pair"
    assert m["seed"] == 11
    assert m["notes"] == "smoke"
    recorded = m["instances"]
    assert isinstance(recorded, list)
    assert len(recorded) == len(insts)
    assert all(len(str(r["sequence_sha256_16"])) == 16 for r in recorded)
    assert isinstance(m["commit"], str) and m["commit"]


def test_manifest_hash_changes_with_the_weights():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    other = {**PAIRS, next(iter(PAIRS)): 0.123}
    a = manifest(insts, obj, PAIRS, seed=1)
    b = manifest(insts, obj, other, seed=1)
    assert a["weights_sha256_16"] != b["weights_sha256_16"]


def test_manifest_hash_changes_with_the_instance_set():
    obj = codon_pair_objective(PAIRS)
    a = manifest(three_instances(), obj, PAIRS, seed=1)
    b = manifest(three_instances()[:2], obj, PAIRS, seed=1)
    assert a["instance_set_sha256_16"] != b["instance_set_sha256_16"]


def test_scores_are_finite():
    obj = codon_pair_objective(PAIRS)
    cai = cai_objective(WEIGHTS)
    for seq in all_allowed(inst())[:25]:
        assert math.isfinite(obj.score(seq))
        assert math.isfinite(cai.score(seq))


# --- gap_closed --------------------------------------------------------------


def test_gap_closed_is_one_at_the_optimum():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    rows = run_strategy(insts, obj, "exact", lambda i: [exact_codon_pair(i, PAIRS)])
    for r in rows:
        assert r.gap_closed == pytest.approx(1.0)


def test_gap_closed_is_zero_for_leaving_the_sequence_alone():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    rows = run_strategy(insts, obj, "original", lambda i: [i.parent.sequence])
    for r in rows:
        assert r.gap_closed == pytest.approx(0.0)


def test_gap_closed_is_negative_when_a_strategy_makes_things_worse():
    """A log-ratio objective can go below the original, which gap_closed shows."""
    insts = three_instances()
    negative = {k: v - 0.5 for k, v in PAIRS.items()}
    obj = codon_pair_objective(negative)
    rows = run_strategy(insts, obj, "random", lambda i: [random_synonymous(i, seed=4)])
    worse = [r for r in rows if r.score is not None and r.score < r.parent_score]
    assert worse, "expected at least one instance made worse"
    assert all(r.gap_closed is not None and r.gap_closed < 0 for r in worse)


def test_gap_closed_never_exceeds_one():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    for rows in compare(insts, obj, strategies(PAIRS)).values():
        for r in rows:
            assert r.gap_closed is None or r.gap_closed <= 1.0 + 1e-12


def test_gap_closed_is_none_without_a_candidate():
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    rows = run_strategy(insts, obj, "barren", lambda i: [])
    assert all(r.gap_closed is None for r in rows)


def test_gap_closed_is_none_when_the_original_is_already_optimal():
    obj = codon_pair_objective(PAIRS)
    i = three_instances()[0]
    best = exact_codon_pair(i, PAIRS)
    already = Instance(key="opt", parent=Dna(sequence=best))
    rows = run_strategy([already], obj, "exact", lambda x: [exact_codon_pair(x, PAIRS)])
    assert rows[0].gap_closed is None


def test_gap_closed_works_where_fraction_of_optimum_misleads():
    """With a negative score and a positive optimum the fraction goes negative."""
    insts = three_instances()
    negative = {k: v - 0.9 for k, v in PAIRS.items()}
    obj = codon_pair_objective(negative)
    rows = run_strategy(insts, obj, "random", lambda i: [random_synonymous(i, seed=6)])
    r = rows[0]
    assert r.score is not None and r.score < 0
    assert r.gap_closed is not None


# --- splitting ---------------------------------------------------------------


def many(n: int) -> list[Instance]:
    return [
        Instance(
            key=f"gene{i}", parent=Dna(sequence="ATG" + "AAA" * (i % 5 + 1) + "TAA")
        )
        for i in range(n)
    ]


def test_split_covers_every_instance_exactly_once():
    insts = many(40)
    sp = split_instances(insts, holdout_fraction=0.3)
    keys = [i.key for i in sp.dev] + [i.key for i in sp.holdout]
    assert sorted(keys) == sorted(i.key for i in insts)
    assert len(keys) == len(set(keys))


def test_split_is_deterministic():
    insts = many(40)
    a = split_instances(insts, holdout_fraction=0.3, salt="s")
    b = split_instances(insts, holdout_fraction=0.3, salt="s")
    assert a.split_hash == b.split_hash
    assert [i.key for i in a.holdout] == [i.key for i in b.holdout]


def test_split_does_not_depend_on_input_order():
    insts = many(40)
    a = split_instances(insts, holdout_fraction=0.3)
    b = split_instances(list(reversed(insts)), holdout_fraction=0.3)
    assert a.split_hash == b.split_hash


def test_adding_an_instance_never_moves_another_across():
    """A reshuffling split would silently turn held-out genes into dev genes."""
    small = many(30)
    grown = many(45)
    a = split_instances(small, holdout_fraction=0.3)
    b = split_instances(grown, holdout_fraction=0.3)
    a_side = {i.key: "holdout" for i in a.holdout} | {i.key: "dev" for i in a.dev}
    b_side = {i.key: "holdout" for i in b.holdout} | {i.key: "dev" for i in b.dev}
    for key, side in a_side.items():
        assert b_side[key] == side, key


def test_salt_changes_the_assignment():
    insts = many(60)
    a = split_instances(insts, holdout_fraction=0.3, salt="one")
    b = split_instances(insts, holdout_fraction=0.3, salt="two")
    assert a.split_hash != b.split_hash
    assert [i.key for i in a.holdout] != [i.key for i in b.holdout]


def test_holdout_fraction_is_approximately_honoured():
    insts = many(400)
    sp = split_instances(insts, holdout_fraction=0.25)
    assert 0.18 < len(sp.holdout) / len(insts) < 0.32


def test_fraction_of_zero_holds_nothing_back_and_one_holds_everything():
    insts = many(30)
    assert split_instances(insts, holdout_fraction=0.0).holdout == ()
    assert split_instances(insts, holdout_fraction=1.0).dev == ()


@pytest.mark.parametrize("fraction", [-0.1, 1.5])
def test_an_impossible_fraction_is_rejected(fraction: float):
    with pytest.raises(ValueError, match="holdout_fraction"):
        split_instances(many(5), holdout_fraction=fraction)


def test_split_hash_tracks_the_division_not_the_sequences():
    a = [Instance(key="x", parent=Dna(sequence="ATGAAATAA"))]
    b = [Instance(key="x", parent=Dna(sequence="ATGAAGTAA"))]
    assert (
        split_instances(a, holdout_fraction=1.0).split_hash
        == split_instances(b, holdout_fraction=1.0).split_hash
    )


def test_summary_lists_both_sides():
    sp = split_instances(many(20), holdout_fraction=0.3)
    s = sp.summary()
    n_dev, n_holdout = s["n_dev"], s["n_holdout"]
    dev_keys = s["dev_keys"]
    assert isinstance(n_dev, int) and isinstance(n_holdout, int)
    assert isinstance(dev_keys, list)
    assert n_dev + n_holdout == 20
    assert len(dev_keys) == n_dev
    assert s["split_hash"] == sp.split_hash


def test_manifest_carries_the_split_hash():
    insts = many(20)
    sp = split_instances(insts, holdout_fraction=0.3)
    obj = codon_pair_objective(PAIRS)
    assert manifest(insts, obj, PAIRS, seed=1)["split_hash"] is None
    assert manifest(insts, obj, PAIRS, seed=1, split=sp)["split_hash"] == sp.split_hash


# --- ledger ------------------------------------------------------------------


def test_ledger_appends_and_reads_back(tmp_path):
    led = Ledger(tmp_path / "ledger")
    led.append("attempt", {"a": 1})
    led.append("attempt", {"a": 2})
    got = [e["a"] for e in led.entries()]
    assert got == [1, 2]
    assert all(e["kind"] == "attempt" for e in led.entries())
    assert all("recorded_at" in e for e in led.entries())


def test_ledger_is_one_file_per_entry(tmp_path):
    led = Ledger(tmp_path / "ledger")
    led.append("attempt", {"a": 1})
    led.append("attempt", {"a": 2})
    assert len(list((tmp_path / "ledger").glob("*.json"))) == 2


def test_ledger_never_rewrites_an_earlier_entry(tmp_path):
    led = Ledger(tmp_path / "ledger")
    first = led.append("attempt", {"a": 1})
    before = first.read_bytes()
    led.append("attempt", {"a": 2})
    assert first.read_bytes() == before


def test_an_empty_ledger_reads_as_empty(tmp_path):
    assert list(Ledger(tmp_path / "nothing").entries()) == []


def test_record_attempt_keeps_per_instance_rows(tmp_path):
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    out = compare(insts, obj, strategies(PAIRS))
    led = Ledger(tmp_path / "ledger")
    path = record_attempt(
        led,
        manifest_=manifest(insts, obj, PAIRS, seed=11),
        split=None,
        results=out,
        selection="none, this is a harness check",
    )
    body = json.loads(path.read_text())
    assert set(body["strategies"]) == set(out)
    exact = body["strategies"]["exact"]
    assert exact["n_instances"] == len(insts)
    assert len(exact["per_instance"]) == len(insts)
    assert exact["mean_gap_closed"] == pytest.approx(1.0)
    assert {r["instance"] for r in exact["per_instance"]} == {i.key for i in insts}


def test_record_attempt_keeps_failures_visible(tmp_path):
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    out = {"barren": run_strategy(insts, obj, "barren", lambda i: [])}
    led = Ledger(tmp_path / "ledger")
    path = record_attempt(
        led, manifest_=manifest(insts, obj, PAIRS, seed=1), split=None, results=out
    )
    body = json.loads(path.read_text())
    rows = body["strategies"]["barren"]
    assert rows["passed"] == 0
    assert rows["n_instances"] == len(insts)
    assert rows["mean_gap_closed"] is None
    assert all(r["score"] is None for r in rows["per_instance"])


def test_record_attempt_stores_dependency_versions(tmp_path):
    insts = three_instances()
    obj = codon_pair_objective(PAIRS)
    led = Ledger(tmp_path / "ledger")
    path = record_attempt(
        led,
        manifest_=manifest(insts, obj, PAIRS, seed=1),
        split=None,
        results={"exact": run_strategy(insts, obj, "e", lambda i: [i.parent.sequence])},
    )
    deps = json.loads(path.read_text())["dependencies"]
    assert deps["pydantic"] != "absent"


def test_dependency_versions_reports_absent_rather_than_raising():
    assert dependency_versions(("definitely-not-installed-xyz",)) == {
        "definitely-not-installed-xyz": "absent"
    }


# --- held-out discipline -----------------------------------------------------


def test_releasing_the_holdout_logs_before_returning_it(tmp_path):
    sp = split_instances(many(20), holdout_fraction=0.3)
    led = Ledger(tmp_path / "ledger")
    got = release_holdout(
        sp,
        reason="frozen confirmation of the exact DP strategy",
        frozen={"strategy": "exact_dp", "dev_mean_gap_closed": 1.0},
        ledger=led,
    )
    assert got == sp.holdout
    accesses = led.holdout_accesses()
    assert len(accesses) == 1
    entry = accesses[0]
    reason, frozen, split_rec = entry["reason"], entry["frozen"], entry["split"]
    assert isinstance(reason, str) and reason.startswith("frozen confirmation")
    assert isinstance(frozen, dict) and frozen["strategy"] == "exact_dp"
    assert isinstance(split_rec, dict)
    assert split_rec["split_hash"] == sp.split_hash


def test_every_release_is_recorded_so_repeats_are_visible(tmp_path):
    sp = split_instances(many(20), holdout_fraction=0.3)
    led = Ledger(tmp_path / "ledger")
    for n in range(3):
        release_holdout(sp, reason=f"look {n}", frozen={"strategy": "s"}, ledger=led)
    assert len(led.holdout_accesses()) == 3


def test_a_release_without_a_reason_is_refused(tmp_path):
    sp = split_instances(many(10), holdout_fraction=0.3)
    led = Ledger(tmp_path / "ledger")
    with pytest.raises(ValueError, match="reason"):
        release_holdout(sp, reason="   ", frozen={"strategy": "s"}, ledger=led)
    assert led.holdout_accesses() == []


def test_a_release_without_anything_frozen_is_refused(tmp_path):
    sp = split_instances(many(10), holdout_fraction=0.3)
    led = Ledger(tmp_path / "ledger")
    with pytest.raises(ValueError, match="frozen"):
        release_holdout(sp, reason="because", frozen={}, ledger=led)
    assert led.holdout_accesses() == []


def test_holdout_accesses_ignores_ordinary_attempts(tmp_path):
    led = Ledger(tmp_path / "ledger")
    led.append("attempt", {"a": 1})
    assert led.holdout_accesses() == []


def test_dev_and_holdout_instances_are_disjoint():
    sp = split_instances(many(60), holdout_fraction=0.4)
    assert not {i.key for i in sp.dev} & {i.key for i in sp.holdout}


# --- weight tables -----------------------------------------------------------


def test_cai_weights_put_the_commonest_synonym_at_one():
    # AAA appears three times, AAG once, so lysine's weights are 1.0 and 1/3.
    seqs = ["ATGAAAAAAAAATAA", "ATGAAGTAA"]
    w = cai_weights_from(seqs)
    assert w["AAA"] == 1.0
    assert w["AAG"] == pytest.approx(1 / 3)


def test_cai_weights_cover_every_codon():
    w = cai_weights_from(["ATGAAAGGCTAA"])
    assert set(w) == set(CODON_TABLE)


def test_an_amino_acid_absent_from_the_reference_gets_zero():
    w = cai_weights_from(["ATGAAATAA"])  # no tryptophan anywhere
    assert w["TGG"] == 0.0


def test_cai_weights_are_in_the_unit_interval():
    w = cai_weights_from([i.parent.sequence for i in three_instances()])
    assert all(0.0 <= v <= 1.0 for v in w.values())


def test_cai_weights_of_nothing_are_all_zero():
    assert set(cai_weights_from([]).values()) == {0.0}


def test_pair_weights_only_cover_pairs_that_occur():
    w = pair_weights_from(["ATGAAAGGCTAA"])
    assert "ATGAAA" in w
    assert "GGCTAA" in w
    assert "ATGGGC" not in w  # never adjacent


def test_pair_weights_are_floored():
    floor = math.log(0.5)
    seqs = [i.parent.sequence for i in three_instances()] * 3
    w = pair_weights_from(seqs, floor=floor)
    assert all(v >= floor - 1e-12 for v in w.values())


def test_pair_weights_of_nothing_are_empty():
    assert pair_weights_from([]) == {}
    assert pair_weights_from(["ATG"]) == {}  # no adjacent pair


def test_derived_weights_drive_the_objectives():
    """The tables the runner derives are usable by the objectives unchanged."""
    reference = [i.parent.sequence for i in three_instances()]
    cai = cai_objective(cai_weights_from(reference))
    pair = codon_pair_objective(pair_weights_from(reference), math.log(0.1))
    i = three_instances()[0]
    assert math.isfinite(cai.score(exact_cai(i, cai_weights_from(reference))))
    assert math.isfinite(pair.score(i.parent.sequence))
