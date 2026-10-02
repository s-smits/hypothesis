from node_dag.nodes.base import BaseNode
from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.types import FooBar


class AtLeast(BaseNode[AtLeastConfig]):
    """Yes when ``count >= config.threshold``."""

    def run(self, value: FooBar) -> bool:
        """Return True for the yes branch."""
        return value.count >= self.config.threshold
