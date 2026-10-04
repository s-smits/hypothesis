from node_dag.dna import codons
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.types import Dna, Score


class CodonCount(BaseNode[CodonCountConfig]):
    """Count the in-frame codons of each sequence that are in ``config.codons``."""

    def run(self, sequence: list[Dna]) -> list[dict[str, Score]]:
        """Return the count for each sequence. In frame, so AAATGGCCC has no ATG."""
        want = set(self.config.codons)
        return [
            {"count": Score(value=sum(c in want for c in codons(s.sequence)))}
            for s in sequence
        ]
