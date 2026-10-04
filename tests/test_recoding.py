"""The target-codon-elimination node, its constraint gate and the lineage mapping."""

from typing import Literal

import pytest
from Bio.Seq import Seq
from pydantic import ValidationError

from node_dag.dna import CODON_TABLE
from node_dag.lineage import Lineage, Variant, outcomes, trace
from node_dag.nodes.tools.constraint_check.config import ConstraintCheckConfig
from node_dag.nodes.tools.constraint_check.function import ConstraintCheck
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.nodes.tools.recode_targeted.config import RecodeTargetedConfig
from node_dag.nodes.tools.recode_targeted.function import RecodeTargeted
from node_dag.types import Dna, Table

# M A L K *, with two leucine codons available to recode and an ATG that is not.
SEQ = Dna(sequence="ATGGCTCTGAAATAA")


def codons(s: str) -> list[str]:
    return [s[i : i + 3] for i in range(0, len(s), 3)]


def recode(
    seq: Dna,
    targets: tuple[str, ...],
    strategy: Literal["random", "first"] = "random",
    seed: int = 0,
) -> Dna:
    cfg = RecodeTargetedConfig(targeted_codons=targets, strategy=strategy, seed=seed)
    return RecodeTargeted(cfg).run(sequence=[seq])[0]


# --- recode_targeted ---------------------------------------------------------


def test_removes_every_occurrence_of_a_target():
    seq = Dna(sequence="ATGCTGCTGCTGTAA")  # three CTG leucines
    out = recode(seq, ("CTG",))
    assert "CTG" not in codons(out.sequence)
    assert str(Seq(out.sequence).translate()) == str(Seq(seq.sequence).translate())


def test_a_partial_codon_at_the_end_is_left_alone():
    (out,) = RecodeTargeted(
        RecodeTargetedConfig(targeted_codons=("TCG",), strategy="first")
    ).run(sequence=[Dna(sequence="ATGTCGTC")])
    assert out.sequence.endswith("TC") and "TCG" not in codons(out.sequence)


def test_leaves_untargeted_codons_alone():
    out = recode(SEQ, ("CTG",))
    before, after = codons(SEQ.sequence), codons(out.sequence)
    assert [b == a for b, a in zip(before, after)] == [True, True, False, True, True]


def test_never_replaces_a_target_with_another_target():
    # Leucine's six codons, with five of them targeted: only CTG can remain.
    targets = ("TTA", "TTG", "CTT", "CTC", "CTA")
    out = recode(Dna(sequence="ATGTTATTGCTTTAA"), targets)
    assert set(codons(out.sequence)) & set(targets) == set()
    assert codons(out.sequence)[1:4] == ["CTG", "CTG", "CTG"]


def test_unreachable_target_is_left_in_place_not_silently_changed():
    """ATG and TGG have no synonym, so the protein must win over the target."""
    seq = Dna(sequence="ATGTGGAAATAA")
    for targets in [("ATG",), ("TGG",)]:
        out = recode(seq, targets)
        assert out.sequence == seq.sequence
        assert str(Seq(out.sequence).translate()) == str(Seq(seq.sequence).translate())


def test_every_leucine_target_is_unreachable_when_all_six_are_targeted():
    targets = ("TTA", "TTG", "CTT", "CTC", "CTA", "CTG")
    out = recode(Dna(sequence="ATGCTGAAATAA"), targets)
    assert out.sequence == "ATGCTGAAATAA"


def test_protein_and_length_preserved_across_a_sweep_of_targets():
    seq = Dna(sequence="ATGGCTCTGAAAGGCTTTCCGTAA")
    protein = str(Seq(seq.sequence).translate())
    for codon in {c for c in codons(seq.sequence)}:
        out = recode(seq, (codon,))
        assert len(out.sequence) == len(seq.sequence), codon
        assert str(Seq(out.sequence).translate()) == protein, codon


def test_first_strategy_is_deterministic_and_ignores_seed():
    a = recode(SEQ, ("CTG",), strategy="first", seed=1)
    b = recode(SEQ, ("CTG",), strategy="first", seed=999)
    assert a.sequence == b.sequence
    assert codons(a.sequence)[2] == "TTA"  # first leucine codon in table order


def test_random_strategy_is_seeded_per_sequence_not_per_batch():
    """A sequence's recoding must not depend on what else is in the list."""
    node = RecodeTargeted(RecodeTargetedConfig(targeted_codons=("CTG",), seed=7))
    other = Dna(sequence="ATGCTGTTTTAA")
    alone = node.run(sequence=[SEQ])[0]
    batched = node.run(sequence=[other, SEQ, other])[1]
    assert alone.sequence == batched.sequence


def test_one_output_per_input_in_order():
    node = RecodeTargeted(RecodeTargetedConfig(targeted_codons=("CTG",)))
    seqs = [SEQ, Dna(sequence="ATGCTGTTTTAA"), Dna(sequence="ATGAAATAA")]
    out = node.run(sequence=seqs)
    assert len(out) == len(seqs)
    assert out[2].sequence == "ATGAAATAA"  # no target present, unchanged


def test_empty_input_gives_empty_output():
    node = RecodeTargeted(RecodeTargetedConfig(targeted_codons=("CTG",)))
    assert node.run(sequence=[]) == []


@pytest.mark.parametrize(
    "targets", [(), ("CT",), ("ctg",), ("CTGA",), ("CTG", "CTG"), ("CTN",)]
)
def test_invalid_targets_rejected(targets: tuple[str, ...]):
    with pytest.raises(ValidationError):
        RecodeTargetedConfig(targeted_codons=targets)


def test_config_is_frozen_and_rejects_extra_fields():
    with pytest.raises(ValidationError):
        RecodeTargetedConfig.model_validate(
            {"targeted_codons": ("CTG",), "nonsense": 1}
        )


def test_strategy_and_seed_change_the_config_hash():
    base = RecodeTargetedConfig(targeted_codons=("CTG",), seed=1)
    assert (
        base.config_hash
        != RecodeTargetedConfig(targeted_codons=("CTG",), seed=2).config_hash
    )
    assert (
        base.config_hash
        != RecodeTargetedConfig(
            targeted_codons=("CTG",), seed=1, strategy="first"
        ).config_hash
    )


# --- constraint_check --------------------------------------------------------


def check(seqs: list[Dna], ref: Dna, targets: tuple[str, ...] = ()) -> list[dict]:
    cfg = ConstraintCheckConfig(reference=ref, targeted_codons=targets)
    rows = ConstraintCheck(cfg).run(sequence=seqs)
    return [{k: v.value for k, v in r.items()} for r in rows]


def test_declared_score_names_exactly():
    (row,) = check([SEQ], SEQ)
    assert set(row) == set(ConstraintCheckConfig.output)


def test_passes_a_clean_recoding():
    out = recode(SEQ, ("CTG",))
    (row,) = check([out], SEQ, ("CTG",))
    assert row == {
        "protein_unchanged": 1.0,
        "length_unchanged": 1.0,
        "targets_remaining": 0.0,
        "targets_unreachable": 0.0,
    }


def test_catches_a_changed_protein():
    (row,) = check([Dna(sequence="ATGGCTCTGAAGTAA")], SEQ)  # K -> K is synonymous
    assert row["protein_unchanged"] == 1.0
    (row,) = check([Dna(sequence="ATGGCTCTGTGGTAA")], SEQ)  # K -> W is not
    assert row["protein_unchanged"] == 0.0


def test_catches_a_changed_length():
    (row,) = check([Dna(sequence="ATGGCTCTGAAA")], SEQ)
    assert row["length_unchanged"] == 0.0


def test_counts_remaining_targets_in_frame_only():
    # CTG sits at offset 5, straddling two codons, so it must not be counted.
    seq = Dna(sequence="ATGGCCTGCAAATAA")
    assert "CTG" in seq.sequence
    assert "CTG" not in codons(seq.sequence)
    (row,) = check([seq], seq, ("CTG",))
    assert row["targets_remaining"] == 0.0


def test_counts_each_remaining_occurrence():
    seq = Dna(sequence="ATGCTGCTGCTGTAA")
    (row,) = check([seq], seq, ("CTG",))
    assert row["targets_remaining"] == 3.0
    assert row["targets_unreachable"] == 0.0


def test_separates_unreachable_from_remaining():
    """ATG cannot be recoded, so it is remaining AND unreachable, never just one."""
    seq = Dna(sequence="ATGCTGAAATAA")
    (row,) = check([seq], seq, ("ATG", "CTG"))
    assert row["targets_remaining"] == 2.0
    assert row["targets_unreachable"] == 1.0  # the ATG only
    after = recode(seq, ("ATG", "CTG"))
    (row,) = check([after], seq, ("ATG", "CTG"))
    assert row["targets_remaining"] == row["targets_unreachable"] == 1.0


def test_no_targets_configured_reports_zero_not_an_error():
    (row,) = check([SEQ], SEQ)
    assert row["targets_remaining"] == 0.0
    assert row["targets_unreachable"] == 0.0


def test_one_row_per_input_in_order_including_failures():
    seqs = [SEQ, Dna(sequence="ATGTGGAAATAA"), Dna(sequence="ATGGCTCTGAAA")]
    rows = check(seqs, SEQ)
    assert len(rows) == len(seqs)
    assert [r["protein_unchanged"] for r in rows] == [1.0, 0.0, 0.0]


def test_columns_match_the_config_hash():
    cfg = ConstraintCheckConfig(reference=SEQ, targeted_codons=("CTG",))
    assert cfg.columns()["targets_remaining"] == (
        f"constraint_check__{cfg.config_hash}__targets_remaining"
    )


# --- lineage -----------------------------------------------------------------

PARENTS = {
    "geneA": Dna(sequence="ATGGCTCTGAAATAA"),
    "geneB": Dna(sequence="ATGGCCCTGAAGTAA"),
}


def pool(variants_per_sequence: int = 20, seed: int = 1):
    cfg = MutateSynonymousConfig(
        seed=seed, count=2, variants_per_sequence=variants_per_sequence
    )
    return trace(PARENTS, MutateSynonymous(cfg))


def test_denominator_is_the_instance_count_not_the_merged_rows():
    lin = pool()
    table = Table.of([v.entity for v in lin.variants])
    assert lin.denominator == 2
    assert len(lin.variants) == 40
    assert len(table.items) < len(lin.variants)  # merging really does lose rows
    assert lin.lost_to_merging() == len(lin.variants) - len(table.items)


def test_collisions_are_reported_rather_than_resolved():
    lin = pool()
    collisions = lin.collisions()
    assert collisions, "expected at least one variant reachable from both genes"
    for instances in collisions.values():
        assert len(instances) > 1
        assert instances <= set(PARENTS)


def test_no_collisions_when_parents_encode_different_proteins():
    parents = {
        "a": Dna(sequence="ATGGCTCTGAAATAA"),
        "b": Dna(sequence="ATGTTTCCGGGCTAA"),
    }
    lin = trace(parents, MutateSynonymous(MutateSynonymousConfig(seed=1, count=2)))
    assert lin.collisions() == {}


def test_by_instance_keeps_every_instance_and_every_duplicate():
    lin = pool()
    by = lin.by_instance()
    assert set(by) == set(PARENTS)
    assert all(len(v) == 20 for v in by.values())


def test_trace_matches_a_batched_call_for_a_sequence_seeded_node():
    cfg = MutateSynonymousConfig(seed=1, count=2, variants_per_sequence=3)
    node = MutateSynonymous(cfg)
    lin = trace(PARENTS, node)
    batched = node.run(sequence=list(PARENTS.values()))
    assert [v.entity.sequence for v in lin.variants] == [b.sequence for b in batched]


def test_outcomes_keeps_an_unscored_instance_in_the_denominator():
    lin = pool(variants_per_sequence=2)
    scored = {v.entity.id for v in lin.by_instance()["geneA"]}
    scores = {i: 1.0 for i in scored}  # geneB scored only where it collides with geneA
    rows = outcomes(lin, scores)
    assert len(rows) == lin.denominator == 2
    assert {r.instance for r in rows} == set(PARENTS)
    a = next(r for r in rows if r.instance == "geneA")
    assert a.scored and a.n_variants == 2 and a.missing == ()


def test_outcomes_reports_missing_scores_by_index():
    lin = pool(variants_per_sequence=2)
    rows = outcomes(lin, {})
    for r in rows:
        assert not r.scored
        assert r.values == ()
        assert r.n_variants == 2
        assert r.missing == (0, 1)


def test_best_returns_none_rather_than_a_number_that_hides_a_failure():
    lin = Lineage(parents={"a": PARENTS["geneA"]}, variants=())
    (row,) = outcomes(lin, {})
    assert row.best(higher_is_better=True) is None
    assert row.best(higher_is_better=False) is None


def test_best_respects_direction():
    v = Variant(instance="a", index=0, entity=PARENTS["geneA"])
    w = Variant(instance="a", index=1, entity=PARENTS["geneB"])
    lin = Lineage(parents={"a": PARENTS["geneA"]}, variants=(v, w))
    scores = {v.entity.id: 2.0, w.entity.id: 5.0}
    (row,) = outcomes(lin, scores)
    assert row.best(higher_is_better=True) == 5.0
    assert row.best(higher_is_better=False) == 2.0


def test_outcomes_on_a_recoding_pool_pairs_parent_to_result():
    node = RecodeTargeted(RecodeTargetedConfig(targeted_codons=("CTG",), seed=3))
    lin = trace(PARENTS, node)
    assert lin.denominator == 2
    for key, variants in lin.by_instance().items():
        (variant,) = variants
        parent = PARENTS[key]
        assert str(Seq(variant.entity.sequence).translate()) == str(
            Seq(parent.sequence).translate()
        )
        assert "CTG" not in codons(variant.entity.sequence)


def test_lineage_survives_a_parent_that_generates_nothing():
    class Barren(RecodeTargeted):
        def run(self, sequence: list[Dna]) -> list[Dna]:
            return []

    node = Barren(RecodeTargetedConfig(targeted_codons=("CTG",)))
    lin = trace(PARENTS, node)
    assert lin.denominator == 2
    assert lin.variants == ()
    assert all(not r.scored and r.n_variants == 0 for r in outcomes(lin, {}))


def test_codon_table_assumption_behind_the_unreachable_tests():
    """The tests above rely on these amino acids having exactly one codon."""
    assert CODON_TABLE["ATG"] == "M"
    assert CODON_TABLE["TGG"] == "W"


def test_a_variant_is_never_its_own_source_but_can_equal_another_input():
    """Two inputs two synonymous swaps apart reach each other; neither is copied through."""
    one, three = Dna(sequence="ATGGCTCTGAAATAA"), Dna(sequence="ATGGCCCTGAAGTAA")
    node = MutateSynonymous(
        MutateSynonymousConfig(seed=7, count=2, variants_per_sequence=30)
    )
    from_one = {d.sequence for d in node.run([one])}
    from_three = {d.sequence for d in node.run([three])}
    assert one.sequence not in from_one and three.sequence not in from_three
    assert three.sequence in from_one and one.sequence in from_three
