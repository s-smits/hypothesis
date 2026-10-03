import random

from node_dag.dna import SYNONYMS
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.types import Dna


class MutateSynonymous(BaseNode[MutateSynonymousConfig]):
    """Swap ``config.count`` random codons for another codon of the same amino acid."""

    def run(self, sequence: Dna) -> Dna:
        """Return the mutated sequence."""
        rng = random.Random(self.config.seed)
        annotated = sequence.codons()
        codons = [c.codon for c in annotated]
        # A codon with no synonym (M, W) has nothing to swap to.
        swappable = [
            i for i, c in enumerate(annotated) if len(SYNONYMS[c.amino_acid]) > 1
        ]
        if self.config.count > len(swappable):
            raise ValueError(
                f"count {self.config.count} > {len(swappable)} codons with a synonym"
            )
        for i in rng.sample(swappable, self.config.count):
            codons[i] = rng.choice(
                [s for s in SYNONYMS[annotated[i].amino_acid] if s != codons[i]]
            )
        return Dna(sequence="".join(codons))
