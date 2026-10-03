from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_complement.function import COMPLEMENT
from node_dag.nodes.tools.dna_reverse_complement.config import (
    DnaReverseComplementConfig,
)
from node_dag.types import Dna


class DnaReverseComplement(BaseNode[DnaReverseComplementConfig]):
    """Give the opposite strand of ``sequence``, 5' to 3'."""

    def run(self, sequence: Dna) -> Dna:
        """Return the complement of the input, read backwards."""
        # One table shared with dna_complement, so the two nodes can never disagree
        # about which base pairs with which.
        return Dna(sequence=sequence.sequence.translate(COMPLEMENT)[::-1])
