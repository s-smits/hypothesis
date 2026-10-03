import pytest
from pydantic import ValidationError

from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.filters.at_most.function import AtMost
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_atom_score.function import DnaAtomScore
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.types import AminoAcidSequence, Dna, Score, Table

REF = Dna(sequence="ATGGCTCTGAAATAA")  # M A L K *


def test_codons_carry_their_amino_acids():
    assert [(c.codon, c.amino_acid) for c in REF.codons()] == [
        ("ATG", "M"),
        ("GCT", "A"),
        ("CTG", "L"),
        ("AAA", "K"),
        ("TAA", "*"),
    ]
    assert REF.protein() == "MALK*"


@pytest.mark.parametrize("bad", ["ATGA", "ATGAAU", "atg"])
def test_dna_rejects_partial_codons_and_other_bases(bad):
    with pytest.raises(ValidationError):
        Dna(sequence=bad)


def test_atom_count():
    assert Dna(sequence="AAA").atom_count() == 99
    assert Dna(sequence="CCC").atom_count() == 93


def test_id_is_the_same_for_the_same_sequence_and_differs_by_kind():
    assert Dna(sequence="ATG").id == Dna(sequence="ATG").id
    assert Dna(sequence="ATG").id != Dna(sequence="ATA").id
    assert Dna(sequence="GCT").id != AminoAcidSequence(sequence="GCT").id
    assert Dna.model_validate_json(REF.model_dump_json()) == REF
    assert REF.model_dump()["id"] == REF.id


def test_each_type_has_a_display_string():
    assert str(REF) == REF.display == "ATG GCT CTG AAA TAA"  # Codons, spaced.
    protein = AminoAcidSequence(sequence="MALK*")
    assert str(protein) == protein.display == "MALK*"
    assert REF.model_dump()["display"] == "ATG GCT CTG AAA TAA"  # The UI reads it.
    assert Dna.model_validate_json(REF.model_dump_json()) == REF


def test_table_merges_repeats_and_keeps_scores_of_what_is_left():
    a, b = Dna(sequence="ATG"), Dna(sequence="AAA")
    t = Table.of([a, b, a], {"c": {a.id: 1.0, b.id: 2.0}})
    assert t.items == [a, b]
    assert Table.of([b], t.scores).scores == {"c": {b.id: 2.0}}


def test_score_gives_atom_count_and_amino_acid_changes():
    node = DnaAtomScore(DnaAtomScoreConfig(reference=REF))
    synonymous = Dna(sequence="ATGGCCCTGAAATAA")
    changed = Dna(sequence="ATGGCTCTGAAACAT")  # stop -> H
    rows = node.run(sequence=[REF, synonymous, changed])
    assert [r["atom_count"] for r in rows] == [
        Score(value=s.atom_count()) for s in (REF, synonymous, changed)
    ]
    assert [r["amino_acid_changes"].value for r in rows] == [0, 0, 1]


def test_score_columns_name_the_node_and_its_hash():
    config = DnaAtomScoreConfig(reference=REF)
    assert config.columns() == {
        "atom_count": f"dna_atom_score__{config.config_hash}__atom_count",
        "amino_acid_changes": f"dna_atom_score__{config.config_hash}__amino_acid_changes",
    }


def test_config_hash_says_what_the_node_does():
    same = DnaAtomScoreConfig(reference=REF)
    assert DnaAtomScoreConfig(reference=REF).config_hash == same.config_hash
    other = DnaAtomScoreConfig(reference=Dna(sequence="ATGGCTCTGAAATGA"))
    assert other.config_hash != same.config_hash
    assert same.model_dump()["config_hash"] == same.config_hash
    # The hash survives a round trip, and a wrong one is rejected.
    assert DnaAtomScoreConfig.model_validate_json(same.model_dump_json()) == same
    with pytest.raises(ValidationError, match="config_hash"):
        DnaAtomScoreConfig(reference=REF, config_hash="deadbeef")
    # It covers every field, and the version.
    assert (
        MutateSynonymousConfig(seed=1).config_hash
        != MutateSynonymousConfig(seed=2).config_hash
    )
    assert (
        MutateSynonymousConfig(seed=1).config_hash != DnaToProteinConfig().config_hash
    )


def test_config_hash_ignores_how_sequences_are_displayed():
    # Saved runs, registries and DAG columns hold these hashes, so they must not change
    # when an entity gains a field that is only for display.
    assert DnaAtomScoreConfig(reference=REF).config_hash == "3745d4af"
    assert "display" in DnaAtomScoreConfig(reference=REF).model_dump()["reference"]


def test_at_most_keeps_values_up_to_the_threshold():
    node = AtMost(AtMostConfig(column="c", threshold=2))
    assert node.run(items=[REF] * 3, values=[1.0, 2.0, 3.0]) == [True, True, False]


@pytest.mark.parametrize("seed", range(20))
def test_mutation_is_synonymous_and_changes_the_requested_codons(seed):
    (out,) = MutateSynonymous(MutateSynonymousConfig(seed=seed, count=2)).run(
        sequence=[REF]
    )
    assert out.protein() == REF.protein()
    assert sum(a != b for a, b in zip(REF.codons(), out.codons())) == 2


def test_mutation_is_deterministic_per_seed_and_per_sequence():
    other = Dna(sequence="GCTGCTGCTGCT")
    node = MutateSynonymous(MutateSynonymousConfig(seed=7, count=2))
    assert node.run(sequence=[REF]) == node.run(sequence=[REF])
    # What else is in the list does not change a sequence's mutant.
    assert node.run(sequence=[other, REF])[1] == node.run(sequence=[REF])[0]


def test_mutation_swaps_all_it_can_when_count_is_more_than_swappable():
    # M has no synonym, so only A, L, K and stop can change.
    (out,) = MutateSynonymous(MutateSynonymousConfig(seed=0, count=5)).run(
        sequence=[REF]
    )
    assert [a != b for a, b in zip(REF.codons(), out.codons())] == [False] + [True] * 4
