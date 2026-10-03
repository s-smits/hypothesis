import math

from node_dag.nodes.base import BaseNode
from node_dag.nodes.filters.pareto_front.config import ParetoFrontConfig
from node_dag.types import Entity


class ParetoFront(BaseNode[ParetoFrontConfig]):
    """Keep each entity that no other entity dominates across ``config.objectives``."""

    def run(self, items: list[Entity], values: dict[str, list[float]]) -> list[bool]:
        """Return True for each entity on the front."""
        objectives = self.config.objectives
        # Orient every objective so that larger is better, then dominance is one rule.
        points = [
            tuple(
                values[col][i] if maximise else -values[col][i]
                for col, maximise in objectives.items()
            )
            for i in range(len(items))
        ]
        finite = [all(math.isfinite(v) for v in p) for p in points]
        return [
            finite[i]
            and not any(
                j != i and finite[j] and _dominates(points[j], points[i])
                for j in range(len(items))
            )
            for i in range(len(items))
        ]


def _dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """Whether ``a`` is at least as good as ``b`` everywhere and better somewhere.

    Equal points do not dominate each other, so duplicates all stay on the front
    rather than one arbitrarily displacing the rest.
    """
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))
