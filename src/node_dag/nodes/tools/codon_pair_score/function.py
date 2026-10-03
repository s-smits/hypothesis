from itertools import pairwise

from node_dag.dna import codons
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.codon_pair_score.config import CodonPairScoreConfig
from node_dag.types import Dna, Score


class CodonPairScore(BaseNode[CodonPairScoreConfig]):
    """Score each sequence's mean weight over adjacent in-frame codon pairs."""

    def run(self, sequence: list[Dna]) -> list[dict[str, Score]]:
        """Return the mean pair weight of each sequence."""
        weights, missing = self.config.pair_weights, self.config.missing
        results = []
        for s in sequence:
            cs = [c for c in codons(s.sequence) if len(c) == 3]
            pairs = [a + b for a, b in pairwise(cs)]
            mean = (
                sum(weights.get(p, missing) for p in pairs) / len(pairs)
                if pairs
                else 0.0
            )
            results.append({"codon_pair": Score(value=mean)})
        return results
