from node_dag.nodes.base import BaseNode
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.types import Entity


class AtMost(BaseNode[AtMostConfig]):
    """Keep each entity whose value is at most ``config.threshold``."""

    def run(self, items: list[Entity], values: list[float]) -> list[bool]:
        """Return True for each entity to keep."""
        return [v <= self.config.threshold for v in values]
