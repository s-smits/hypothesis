from itertools import zip_longest

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.amino_acid_changes.config import AminoAcidChangesConfig
from node_dag.types import Dna, Score


class AminoAcidChanges(BaseNode[AminoAcidChangesConfig]):
    """Count the positions where ``sequence`` codes for a different amino acid."""

    def run(self, sequence: Dna, reference: Dna) -> Score:
        """Return how many amino acids differ, 0 when the change was synonymous."""
        # zip_longest, not zip: a truncated sequence would otherwise match on every
        # position it still has and score 0, which is the one answer it must not give.
        return Score(
            value=sum(
                a != b
                for a, b in zip_longest(sequence.protein(), reference.protein())
            )
        )
