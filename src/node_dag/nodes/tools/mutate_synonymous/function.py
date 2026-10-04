import random

from node_dag.dna import CODON_TABLE, SYNONYMS
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.types import Dna


class MutateSynonymous(BaseNode[MutateSynonymousConfig]):
    """Swap up to ``config.count`` random codons of each sequence for another codon of the same amino acid."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return mutated sequences for each input."""
        results: list[Dna] = []
        for s in sequence:
            for variant_idx in range(self.config.variants_per_sequence):
                results.append(self._mutate(s, variant_idx))
        return results

    def _mutate(self, s: Dna, variant_idx: int = 0) -> Dna:
        # Seed per sequence and variant index. variant_idx 0 preserves legacy seed string.
        seed_key = (
            f"{self.config.seed}:{s.id}"
            if variant_idx == 0
            else f"{self.config.seed}:{variant_idx}:{s.id}"
        )
        rng = random.Random(seed_key)
        codons = [s.sequence[i : i + 3] for i in range(0, len(s.sequence), 3)]
        # A codon with no synonym (M, W) has nothing to swap to. A stop codon stays: its
        # synonyms are the other stops, and swapping one changes where the gene ends.
        swappable = [
            i
            for i, c in enumerate(codons)
            if len(c) == 3
            and CODON_TABLE[c] != "*"
            and len(SYNONYMS[CODON_TABLE[c]]) > 1
        ]
        for i in rng.sample(swappable, min(self.config.count, len(swappable))):
            aa = CODON_TABLE[codons[i]]
            codons[i] = rng.choice([c for c in SYNONYMS[aa] if c != codons[i]])
        return Dna(sequence="".join(codons))
