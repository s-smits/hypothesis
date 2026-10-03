from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_complement.config import DnaComplementConfig
from node_dag.types import Dna

# A to T and C to G, both ways round, so the table is its own inverse.
COMPLEMENT = str.maketrans("ACGT", "TGCA")


class DnaComplement(BaseNode[DnaComplementConfig]):
    """Swap each base of ``sequence`` for its pair, in the same order."""

    def run(self, sequence: Dna) -> Dna:
        """Return the complement, read in the same direction as the input."""
        return Dna(sequence=sequence.sequence.translate(COMPLEMENT))
