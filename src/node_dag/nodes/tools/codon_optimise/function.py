import random

from node_dag.dna import CODON_TABLE, SYNONYMS, codons
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.codon_optimise.config import CodonOptimiseConfig
from node_dag.types import Dna


class CodonOptimise(BaseNode[CodonOptimiseConfig]):
    """Respell each sequence's codons by ``config.strategy`` over the weight table."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return one recoded sequence per input."""
        return [self._recode(s) for s in sequence]

    def _recode(self, s: Dna) -> Dna:
        rng = random.Random(f"{self.config.seed}:{s.id}")
        return Dna(sequence="".join(self._pick(c, rng) for c in codons(s.sequence)))

    def _pick(self, codon: str, rng: random.Random) -> str:
        if len(codon) != 3:
            return codon
        synonyms = SYNONYMS[CODON_TABLE[codon]]
        weights = [self.config.codon_weights.get(s, 0.0) for s in synonyms]
        if len(set(weights)) == 1:
            # Every synonym weighs the same, so the table says nothing here.
            return codon
        match self.config.strategy:
            case "most_frequent":
                return synonyms[weights.index(max(weights))]
            case "least_frequent":
                return synonyms[weights.index(min(weights))]
            case _:
                return rng.choices(synonyms, weights=weights)[0]
