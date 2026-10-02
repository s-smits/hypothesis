from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.sum.config import SumConfig
from node_dag.types import FooBar


class Sum(BaseNode[SumConfig]):
    """Return ``(a.count + b.count) * config.scale``."""

    def run(self, a: FooBar, b: FooBar) -> FooBar:
        """Return the scaled total."""
        return FooBar(count=(a.count + b.count) * self.config.scale)
