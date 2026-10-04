import random

from node_dag.dna import CODON_TABLE, SYNONYMS, codons
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.resample_synonymous.config import ResampleSynonymousConfig
from node_dag.types import Dna


class ResampleSynonymous(BaseNode[ResampleSynonymousConfig]):
    """Redraw every synonymous codon of each sequence uniformly at random."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return ``variants_per_sequence`` resampled sequences per input."""
        results: list[Dna] = []
        for s in sequence:
            for variant_idx in range(self.config.variants_per_sequence):
                results.append(self._resample(s, variant_idx))
        return results

    def _resample(self, s: Dna, variant_idx: int = 0) -> Dna:
        # Seed per sequence and variant index. variant_idx 0 preserves the
        # legacy seed string shared with mutate_synonymous.
        seed_key = (
            f"{self.config.seed}:{s.id}"
            if variant_idx == 0
            else f"{self.config.seed}:{variant_idx}:{s.id}"
        )
        rng = random.Random(seed_key)
        out = []
        for c in codons(s.sequence):
            # A stop codon stays: swapping it for another stop changes where the gene ends.
            keep = len(c) != 3 or CODON_TABLE[c] == "*"
            synonyms = (c,) if keep else SYNONYMS[CODON_TABLE[c]]
            out.append(rng.choice(synonyms))
        return Dna(sequence="".join(out))
