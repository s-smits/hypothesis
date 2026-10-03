from node_dag.nodes.base import BaseNode
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.types import Entity


class AtLeast(BaseNode[AtLeastConfig]):
    """Keep each entity whose value is at least ``config.threshold``."""

    def run(self, items: list[Entity], values: list[float]) -> list[bool]:
        """Return True for each entity to keep."""
        return [v >= self.config.threshold for v in values]
