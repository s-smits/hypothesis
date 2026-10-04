import random

from node_dag.dna import (
    CODON_TABLE,
    SYNONYMS,
    codons,
    motif_hits,
    reverse_complement,
)
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.domesticate.config import DomesticateConfig
from node_dag.types import Dna


class Domesticate(BaseNode[DomesticateConfig]):
    """Remove each sequence's unwanted motifs by synonymous substitution."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return one domesticated sequence per input."""
        return [self._clean(s) for s in sequence]

    def _clean(self, s: Dna) -> Dna:
        sites = set(self.config.motifs)
        if self.config.both_strands:
            sites |= {reverse_complement(m) for m in self.config.motifs}
        rng = random.Random(f"{self.config.seed}:{s.id}")
        cs = codons(s.sequence)
        # Every accepted swap strictly reduces the hit count, so this ends.
        while hits := motif_hits("".join(cs), sites):
            if not self._fix_any(cs, hits, sites, rng):
                break
        return Dna(sequence="".join(cs))

    def _fix_any(
        self,
        cs: list[str],
        hits: list[tuple[int, int]],
        sites: set[str],
        rng: random.Random,
    ) -> bool:
        """Fix one hit if any can be fixed, working left to right."""
        for start, end in hits:
            positions = list(range(start // 3, (end - 1) // 3 + 1))
            if self.config.strategy == "random":
                rng.shuffle(positions)
            for i in positions:
                if self._fix_at(cs, i, sites, rng):
                    return True
        return False

    def _fix_at(
        self, cs: list[str], i: int, sites: set[str], rng: random.Random
    ) -> bool:
        """Swap codon ``i`` for a synonym that leaves the touched region clean."""
        codon = cs[i]
        # A stop stays: its synonyms are the other stops, and swapping one changes where
        # the gene ends.
        if len(codon) != 3 or CODON_TABLE[codon] == "*":
            return False
        synonyms = [s for s in SYNONYMS[CODON_TABLE[codon]] if s != codon]
        if self.config.strategy == "random":
            rng.shuffle(synonyms)
        span = max(len(m) for m in sites) - 1
        for s in synonyms:
            cs[i] = s
            joined = "".join(cs)
            # A motif can only appear or disappear where it covers a changed
            # base, so only the region reaching one motif-length out is checked.
            region = joined[max(0, 3 * i - span) : 3 * i + 3 + span]
            if not motif_hits(region, sites):
                return True
            cs[i] = codon
        return False
