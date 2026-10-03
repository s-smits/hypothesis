from itertools import zip_longest

from Bio.Seq import Seq

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.types import Dna, Score


class DnaAtomScore(BaseNode[DnaAtomScoreConfig]):
    """Score atom count, and amino acid changes from ``config.reference``."""

    def run(self, sequence: list[Dna]) -> list[dict[str, Score]]:
        """Return the scores of each sequence."""
        ref = str(Seq(self.config.reference.sequence).translate())
        return [
            {
                "atom_count": Score(value=s.atom_count()),
                "amino_acid_changes": Score(
                    value=sum(
                        a != b
                        for a, b in zip_longest(str(Seq(s.sequence).translate()), ref)
                    )
                ),
            }
            for s in sequence
        ]

