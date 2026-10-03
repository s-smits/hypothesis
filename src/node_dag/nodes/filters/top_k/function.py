import math

from node_dag.nodes.base import BaseNode
from node_dag.nodes.filters.top_k.config import TopKConfig
from node_dag.types import Entity


class TopK(BaseNode[TopKConfig]):
    """Keep the ``config.k`` entities whose value ranks best in ``config.column``."""

    def run(self, items: list[Entity], values: list[float]) -> list[bool]:
        """Return True for each entity to keep."""
        # Rank by value, then by entity id, so the selection does not depend on the
        # order the entities arrived in. Non-finite values are left out entirely:
        # ranking them would let a failed scorer win on a NaN or an infinity.
        ranked = sorted(
            (i for i, v in enumerate(values) if math.isfinite(v)),
            key=lambda i: (
                -values[i] if self.config.largest else values[i],
                items[i].id,
            ),
        )
        keep = set(ranked[: self.config.k])
        return [i in keep for i in range(len(items))]
