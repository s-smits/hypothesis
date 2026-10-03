from node_dag.nodes.base import BaseNode
from node_dag.nodes.decisions.codons_absent.config import CodonsAbsentConfig
from node_dag.types import Dna


class CodonsAbsent(BaseNode[CodonsAbsentConfig]):
    """Yes when no codon of ``sequence`` is one of ``config.codons``."""

    def run(self, sequence: Dna) -> bool:
        """Return True for the yes branch."""
        # In frame, via codons(): a substring test on sequence.sequence would match
        # across a codon boundary and take the wrong branch.
        targets = set(self.config.codons)
        return not any(c.codon in targets for c in sequence.codons())
