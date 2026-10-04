import random

from node_dag.dna import SYNONYMS
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.protein_to_dna.config import ProteinToDnaConfig
from node_dag.types import AminoAcidSequence, Dna


class ProteinToDna(BaseNode[ProteinToDnaConfig]):
    """Choose a codon for each amino acid by ``config.strategy`` over the weights."""

    def run(self, sequence: list[AminoAcidSequence]) -> list[Dna]:
        """Return one coding sequence per protein."""
        return [self._encode(s) for s in sequence]

    def _encode(self, s: AminoAcidSequence) -> Dna:
        rng = random.Random(f"{self.config.seed}:{s.id}")
        return Dna(sequence="".join(self._pick(aa, rng) for aa in s.sequence))

    def _pick(self, amino_acid: str, rng: random.Random) -> str:
        codons = SYNONYMS[amino_acid]
        weights = [self.config.codon_weights.get(c, 0.0) for c in codons]
        if len(set(weights)) == 1:
            # Every codon weighs the same, so the table says nothing here.
            return codons[0]
        match self.config.strategy:
            case "most_frequent":
                return codons[weights.index(max(weights))]
            case "least_frequent":
                return codons[weights.index(min(weights))]
            case _:
                return rng.choices(codons, weights=weights)[0]
