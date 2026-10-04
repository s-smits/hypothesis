import math

from node_dag.nodes.base import BaseNode
from node_dag.nodes.filters.beats_reference.config import BeatsReferenceConfig
from node_dag.types import Entity


class BeatsReference(BaseNode[BeatsReferenceConfig]):
    """Keep each entity whose value is strictly better than the reference's."""

    def run(
        self, items: list[Entity], values: list[float], reference: float
    ) -> list[bool]:
        """Return True for each entity to keep."""
        if self.config.higher:
            return [math.isfinite(v) and v > reference for v in values]
        return [math.isfinite(v) and v < reference for v in values]
