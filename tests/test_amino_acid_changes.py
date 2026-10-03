"""Turning "the protein is unchanged" into evidence a decision branch can carry.

Until this node existed, the only thing that noticed a non-synonymous change was
``dna_atom_score`` raising, which fails a step instead of producing a value. An
assertion cannot name an exception, so the criterion could not be checked.
"""

import pytest

from node_dag.nodes.decisions.at_most.config import AtMostConfig
from node_dag.nodes.decisions.at_most.function import AtMost
from node_dag.nodes.tools.amino_acid_changes.config import AminoAcidChangesConfig
from node_dag.nodes.tools.amino_acid_changes.function import AminoAcidChanges
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons
from node_dag.types import Dna

GENE = Dna(sequence="ATGTCGTCAGCTTAA")  # MSSA*


def _changes(sequence: str, reference: Dna = GENE) -> float:
    node = AminoAcidChanges(AminoAcidChangesConfig())
    return node.run(sequence=Dna(sequence=sequence), reference=reference).value


def test_an_identical_sequence_has_no_changes():
    assert _changes(GENE.sequence) == 0


def test_a_synonymous_recoding_has_no_changes():
    recoded = RecodeCodons(RecodeCodonsConfig(targets=("TCG", "TCA"))).run(sequence=GENE)
    assert recoded.sequence != GENE.sequence
    assert _changes(recoded.sequence) == 0


@pytest.mark.parametrize(
    ("sequence", "changes"),
    [
        ("ATGGGGTCAGCTTAA", 1),  # S -> G at position 2
        ("ATGGGGGGGGCTTAA", 2),  # both serines become glycine
        ("ATGTCGTCAGCTTGG", 1),  # the stop becomes a tryptophan
    ],
)
def test_a_changed_amino_acid_is_counted(sequence, changes):
    assert _changes(sequence) == changes


def test_a_truncated_sequence_counts_the_missing_positions():
    """The one answer it must never give for a truncation is zero."""
    assert _changes("ATGTCGTCA") == 2


def test_at_most_zero_turns_the_count_into_a_branch():
    """The point of the node: a criterion a plan can assert against.

    ``amino_acid_changes`` then ``at_most(threshold=0)`` gives a decision whose yes
    branch means the protein survived, which is exactly the shape an Assertion needs.
    """
    gate = AtMost(AtMostConfig(threshold=0))
    recoded = RecodeCodons(RecodeCodonsConfig(targets=("TCG", "TCA"))).run(sequence=GENE)
    scorer = AminoAcidChanges(AminoAcidChangesConfig())

    synonymous = scorer.run(sequence=recoded, reference=GENE)
    assert gate.run(value=synonymous) is True

    broken = scorer.run(sequence=Dna(sequence="ATGGGGTCAGCTTAA"), reference=GENE)
    assert gate.run(value=broken) is False
