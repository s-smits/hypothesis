from Bio.Seq import Seq

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_reverse_complement.config import (
    DnaReverseComplementConfig,
)
from node_dag.types import Dna


class DnaReverseComplement(BaseNode[DnaReverseComplementConfig]):
    """Give the opposite strand of each sequence, 5' to 3'."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return one reverse complement per DNA sequence."""
        return [
            Dna(sequence=str(Seq(s.sequence).reverse_complement())) for s in sequence
        ]
