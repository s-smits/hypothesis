from node_dag.nodes.base import BaseNode
from node_dag.nodes.decisions.at_most.config import AtMostConfig
from node_dag.types import Score


class AtMost(BaseNode[AtMostConfig]):
    """Yes when ``value <= config.threshold``."""

    def run(self, value: Score) -> bool:
        """Return True for the yes branch."""
        return value.value <= self.config.threshold
