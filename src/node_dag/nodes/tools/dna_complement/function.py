from Bio.Seq import Seq

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_complement.config import DnaComplementConfig
from node_dag.types import Dna


class DnaComplement(BaseNode[DnaComplementConfig]):
    """Swap each base of each sequence for its pair."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return one complement per DNA sequence."""
        return [Dna(sequence=str(Seq(s.sequence).complement())) for s in sequence]
