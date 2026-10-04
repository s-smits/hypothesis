from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.trim_to_first_start.config import TrimToFirstStartConfig
from node_dag.types import Dna


class TrimToFirstStart(BaseNode[TrimToFirstStartConfig]):
    """Cut each sequence at its first ATG and its tail to whole codons."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return the ORF of each sequence that has an ATG, in input order."""
        return [o for s in sequence if (o := self._trim(s)) is not None]

    def _trim(self, s: Dna) -> Dna | None:
        i = s.sequence.find("ATG")
        if i < 0:
            return None
        orf = s.sequence[i:]
        return Dna(sequence=orf[: len(orf) - len(orf) % 3])
