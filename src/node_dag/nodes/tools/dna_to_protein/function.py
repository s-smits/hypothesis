from Bio.Seq import Seq

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.types import AminoAcidSequence, Dna


class DnaToProtein(BaseNode[DnaToProteinConfig]):
    """Translate each DNA sequence to the amino acids it encodes."""

    def run(self, sequence: list[Dna]) -> list[AminoAcidSequence]:
        """Return one amino acid sequence per DNA sequence."""
        return [
            AminoAcidSequence(sequence=str(Seq(s.sequence).translate()))
            for s in sequence
        ]
