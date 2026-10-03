import random

from node_dag.dna import SYNONYMS
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.types import Dna


class MutateSynonymous(BaseNode[MutateSynonymousConfig]):
    """Swap up to ``config.count`` random codons of each sequence for another codon of the same amino acid."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return one mutated sequence per input."""
        return [self._mutate(s) for s in sequence]

    def _mutate(self, s: Dna) -> Dna:
        # Seed per sequence, so a sequence mutates the same whatever else is in the list.
        rng = random.Random(f"{self.config.seed}:{s.id}")
        annotated = s.codons()
        codons = [c.codon for c in annotated]
        # A codon with no synonym (M, W) has nothing to swap to.
        swappable = [
            i for i, c in enumerate(annotated) if len(SYNONYMS[c.amino_acid]) > 1
        ]
        for i in rng.sample(swappable, min(self.config.count, len(swappable))):
            codons[i] = rng.choice(
                [c for c in SYNONYMS[annotated[i].amino_acid] if c != codons[i]]
            )
        return Dna(sequence="".join(codons))
