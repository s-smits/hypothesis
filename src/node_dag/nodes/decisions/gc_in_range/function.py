from node_dag.nodes.base import BaseNode
from node_dag.nodes.decisions.gc_in_range.config import GcInRangeConfig
from node_dag.types import Dna


def gc_fraction(bases: str) -> float:
    """The fraction of ``bases`` that are G or C, 0 for an empty string."""
    return sum(b in "GC" for b in bases) / len(bases) if bases else 0.0


class GcInRange(BaseNode[GcInRangeConfig]):
    """Yes when every checked window of ``sequence`` has GC within the range."""

    def run(self, sequence: Dna) -> bool:
        """Return True when no window falls outside ``low``..``high``.

        With no window the whole sequence is one check. With a window every position
        is checked, because a GC-rich patch is what defeats synthesis and a sequence
        can carry one while averaging perfectly well overall.
        """
        s = sequence.sequence
        size = self.config.window
        if not size or size >= len(s):
            return self.config.low <= gc_fraction(s) <= self.config.high
        return all(
            self.config.low <= gc_fraction(s[i : i + size]) <= self.config.high
            for i in range(len(s) - size + 1)
        )
