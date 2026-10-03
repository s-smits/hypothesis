from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.start_region_composition.config import (
    StartRegionCompositionConfig,
)
from node_dag.types import Dna, Score


class StartRegionComposition(BaseNode[StartRegionCompositionConfig]):
    """Score the start of ``sequence`` by how far A outweighs G."""

    def run(self, sequence: Dna) -> Score:
        """Return the A fraction less the G fraction over the first codons.

        A short sequence is scored over whatever it has rather than refused: the rule
        is about the start, and a sequence shorter than the window is all start.
        """
        region = sequence.sequence[: self.config.codons * 3]
        if not region:
            return Score(value=0.0)
        a, g = region.count("A"), region.count("G")
        return Score(value=(a - g) / len(region))
