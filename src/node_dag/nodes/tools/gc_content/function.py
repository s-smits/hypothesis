from node_dag.dna import gc_fraction, gc_window_fractions
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.gc_content.config import GcContentConfig
from node_dag.types import NucleicAcid, Score


class GcContent(BaseNode[GcContentConfig]):
    """Score each sequence's GC, overall and across sliding windows."""

    def run(self, sequence: list[NucleicAcid]) -> list[dict[str, Score]]:
        """Return the GC scores of each sequence."""
        return [self._scores(s) for s in sequence]

    def _scores(self, s: NucleicAcid) -> dict[str, Score]:
        fracs = gc_window_fractions(s.sequence, self.config.window)
        return {
            "gc": Score(value=gc_fraction(s.sequence)),
            "gc_min": Score(value=min(fracs)),
            "gc_max": Score(value=max(fracs)),
            "gc_deviation": Score(
                value=max(abs(f - self.config.target) for f in fracs)
            ),
        }
