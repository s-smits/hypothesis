from node_dag.nodes.base import BaseNode
from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.types import Score


class AtLeast(BaseNode[AtLeastConfig]):
    """Yes when ``value >= config.threshold``."""

    def run(self, value: Score) -> bool:
        """Return True for the yes branch."""
        return value.value >= self.config.threshold
