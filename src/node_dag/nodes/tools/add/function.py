from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.add.config import AddConfig
from node_dag.types import FooBar


class Add(BaseNode[AddConfig]):
    """Add ``config.amount`` to ``count``."""

    def run(self, value: FooBar) -> FooBar:
        """Return a FooBar with the amount added."""
        return FooBar(count=value.count + self.config.amount)
