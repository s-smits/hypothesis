from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.types import AminoAcidSequence, Dna


class DnaToProtein(BaseNode[DnaToProteinConfig]):
    """Convert a DNA sequence to its amino acid representation."""

    def run(self, sequence: Dna) -> AminoAcidSequence:
        """Return the amino acid sequence encoded by the DNA."""
        return AminoAcidSequence(sequence=sequence.protein())
