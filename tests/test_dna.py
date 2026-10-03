import pytest
from pydantic import ValidationError

from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_atom_score.function import DnaAtomScore
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.types import Dna, Score

REF = Dna(sequence="ATGGCTCTGAAATAA")  # M A L K *


def test_codons_carry_their_amino_acids():
    assert [(c.codon, c.amino_acid) for c in REF.codons()] == [
        ("ATG", "M"),
        ("GCT", "A"),
        ("CTG", "L"),
        ("AAA", "K"),
        ("TAA", "*"),
    ]
    assert REF.protein() == "MAL" + "K*"


@pytest.mark.parametrize("bad", ["ATGA", "ATGAAU", "atg"])
def test_dna_rejects_partial_codons_and_other_bases(bad):
    with pytest.raises(ValidationError):
        Dna(sequence=bad)


def test_atom_count():
    assert Dna(sequence="AAA").atom_count() == 99
    assert Dna(sequence="CCC").atom_count() == 93


def test_score_is_atom_count():
    node = DnaAtomScore(DnaAtomScoreConfig())
    assert node.run(sequence=REF, reference=REF) == Score(value=REF.atom_count())


def test_score_rejects_a_changed_amino_acid():
    node = DnaAtomScore(DnaAtomScoreConfig())
    with pytest.raises(ValueError, match="Not synonymous"):
        node.run(sequence=Dna(sequence="ATGGCTCTGAAACAT"), reference=REF)  # stop -> H


@pytest.mark.parametrize("seed", range(20))
def test_mutation_is_synonymous_and_changes_the_requested_codons(seed):
    out = MutateSynonymous(MutateSynonymousConfig(seed=seed, count=2)).run(sequence=REF)
    assert out.protein() == REF.protein()
    changed = [a != b for a, b in zip(REF.codons(), out.codons())]
    assert sum(changed) == 2


def test_mutation_is_deterministic_per_seed():
    node = MutateSynonymous(MutateSynonymousConfig(seed=7, count=2))
    assert node.run(sequence=REF) == node.run(sequence=REF)


def test_mutation_rejects_more_swaps_than_swappable_codons():
    # M has no synonym, so only A, L, K and stop can change.
    with pytest.raises(ValueError, match="with a synonym"):
        MutateSynonymous(MutateSynonymousConfig(seed=0, count=5)).run(sequence=REF)
