from node_dag.dna import CODON_TABLE, SYNONYMS, codons
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.types import Dna


class RecodeCodons(BaseNode[RecodeCodonsConfig]):
    """Swap every in-frame target codon for a synonym that is not a target."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return the recoded sequences, or raise when an amino acid has no way out."""
        bad = set(self.config.targets)
        out = []
        for s in sequence:
            frame = codons(s.sequence)
            # SYNONYMS has a fixed order, so the first non-target synonym is deterministic.
            swaps: dict[str, str] = {}
            for c in frame:
                if c in bad and c not in swaps:
                    swap = next(
                        (x for x in SYNONYMS[CODON_TABLE[c]] if x not in bad), None
                    )
                    if swap is None:
                        raise ValueError(
                            f"Every synonym of a target codon is also a target: {sorted(bad)}"
                        )
                    swaps[c] = swap
            tail = s.sequence[
                len(frame) * 3 :
            ]  # A partial codon at the end stays as it is.
            out.append(Dna(sequence="".join(swaps.get(c, c) for c in frame) + tail))
        return out
