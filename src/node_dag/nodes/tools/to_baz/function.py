from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.to_baz.config import ToBazConfig
from node_dag.types import Baz, FooBar


class ToBaz(BaseNode[ToBazConfig]):
    """Label a FooBar's count."""

    def run(self, value: FooBar) -> Baz:
        """Return the Baz."""
        return Baz(label=f"{self.config.prefix}{value.count}")
