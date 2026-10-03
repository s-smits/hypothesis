from node_dag.dna import SYNONYMS
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.types import Dna


class RecodeCodons(BaseNode[RecodeCodonsConfig]):
    """Swap every in-frame ``config.targets`` codon for a synonym that is not a target."""

    def run(self, sequence: Dna) -> Dna:
        """Return the recoded sequence, or raise when an amino acid has no way out."""
        targets = set(self.config.targets)
        codons: list[str] = []
        for codon in sequence.codons():
            if codon.codon not in targets:
                codons.append(codon.codon)
                continue
            # Deterministic: dna.py builds CODON_TABLE from product(BASES, repeat=3)
            # and filters SYNONYMS in that order, so "first" is a fixed codon, not
            # whatever a set happened to yield.
            swap = next(
                (s for s in SYNONYMS[codon.amino_acid] if s not in targets), None
            )
            if swap is None:
                raise ValueError(
                    f"Every synonymous codon of {codon.amino_acid} is targeted: "
                    f"{SYNONYMS[codon.amino_acid]}"
                )
            codons.append(swap)
        return Dna(sequence="".join(codons))
