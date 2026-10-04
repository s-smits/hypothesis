from Bio.Seq import Seq

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.constraint_check.config import ConstraintCheckConfig
from node_dag.nodes.tools.recode_targeted.function import untargeted_synonyms
from node_dag.types import Dna, Score


class ConstraintCheck(BaseNode[ConstraintCheckConfig]):
    """Report each sequence's hard-constraint outcomes against ``config.reference``."""

    def run(self, sequence: list[Dna]) -> list[dict[str, Score]]:
        """Return the constraint outcomes of each sequence."""
        ref = self.config.reference.sequence
        ref_protein = str(Seq(ref).translate())
        targeted = frozenset(self.config.targeted_codons)
        return [self._check(s, ref, ref_protein, targeted) for s in sequence]

    def _check(
        self, s: Dna, ref: str, ref_protein: str, targeted: frozenset[str]
    ) -> dict[str, Score]:
        codons = [s.sequence[i : i + 3] for i in range(0, len(s.sequence), 3)]
        remaining = [c for c in codons if len(c) == 3 and c in targeted]
        unreachable = [c for c in remaining if not untargeted_synonyms(c, targeted)]
        return {
            "protein_unchanged": Score(
                value=float(str(Seq(s.sequence).translate()) == ref_protein)
            ),
            "length_unchanged": Score(value=float(len(s.sequence) == len(ref))),
            # Compared codon for codon, so a synonymous stop counts as a change: it
            # keeps the protein but moves a position the goal said to keep.
            "immutable_unchanged": Score(
                value=float(
                    all(
                        s.sequence[3 * i : 3 * i + 3] == ref[3 * i : 3 * i + 3]
                        for i in self.config.immutable
                    )
                )
            ),
            "immutable_checked": Score(value=len(self.config.immutable)),
            "targets_remaining": Score(value=len(remaining)),
            "targets_unreachable": Score(value=len(unreachable)),
        }
