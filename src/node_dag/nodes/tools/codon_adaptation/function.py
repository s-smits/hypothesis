import math

from node_dag.dna import CODON_TABLE, SYNONYMS, codons
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.codon_adaptation.config import CodonAdaptationConfig
from node_dag.types import Dna, Score


class CodonAdaptation(BaseNode[CodonAdaptationConfig]):
    """Score each sequence's codon adaptation index over the weight table."""

    def run(self, sequence: list[Dna]) -> list[dict[str, Score]]:
        """Return the CAI of each sequence."""
        return [self._cai(s) for s in sequence]

    def _cai(self, s: Dna) -> dict[str, Score]:
        weights = self.config.codon_weights
        log_total, n = 0.0, 0
        for c in codons(s.sequence):
            if len(c) != 3 or CODON_TABLE[c] == "*":
                continue
            top = max(weights.get(x, 0.0) for x in SYNONYMS[CODON_TABLE[c]])
            if top == 0:
                continue  # The table says nothing about this amino acid.
            w = weights.get(c, 0.0) / top
            if w == 0:
                # A zero term empties the geometric mean's whole product.
                return {"cai": Score(value=0.0)}
            log_total += math.log(w)
            n += 1
        return {"cai": Score(value=math.exp(log_total / n) if n else 0.0)}
