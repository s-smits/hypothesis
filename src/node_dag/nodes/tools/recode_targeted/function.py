import random

from node_dag.dna import CODON_TABLE, SYNONYMS
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.recode_targeted.config import RecodeTargetedConfig
from node_dag.types import Dna


def untargeted_synonyms(codon: str, targeted: frozenset[str]) -> tuple[str, ...]:
    """The synonyms of ``codon`` that are neither ``codon`` itself nor targeted.

    Empty when the amino acid has no way out of the targeted set, so a caller can tell
    an unreachable codon from one its strategy merely failed to replace.
    """
    return tuple(
        c for c in SYNONYMS[CODON_TABLE[codon]] if c != codon and c not in targeted
    )


class RecodeTargeted(BaseNode[RecodeTargetedConfig]):
    """Replace every in-frame targeted codon with an untargeted synonym."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return one recoded sequence for each input."""
        return [self._recode(s) for s in sequence]

    def _recode(self, s: Dna) -> Dna:
        targeted = frozenset(self.config.targeted_codons)
        # Seeded by configuration seed and sequence id, as mutate_synonymous is, so
        # batch composition cannot change a sequence's recoding.
        rng = random.Random(f"{self.config.seed}:{s.id}")
        codons = [s.sequence[i : i + 3] for i in range(0, len(s.sequence), 3)]
        for i, codon in enumerate(codons):
            if len(codon) != 3 or codon not in targeted:
                continue
            alts = untargeted_synonyms(codon, targeted)
            if not alts:
                continue  # No untargeted synonym: left in place, counted downstream.
            codons[i] = (
                rng.choice(alts) if self.config.strategy == "random" else alts[0]
            )
        return Dna(sequence="".join(codons))
