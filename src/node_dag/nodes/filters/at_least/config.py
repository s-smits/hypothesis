from typing import ClassVar, Literal

from node_dag.nodes.base import BaseFilterConfig, Category
from node_dag.types import Entity


class AtLeastConfig(BaseFilterConfig):
    """Keep the entities whose score in ``column`` is at least ``threshold``.

    The rest go to the no branch.

    Args:
        column: The score column to filter on, ``<node name>__<config hash>__<score name>``.
        threshold: The lowest score that is kept.
    """

    name: Literal["at_least"] = "at_least"
    threshold: float
    categories = (Category.FILTER,)
    inputs: ClassVar = {"items": Entity}
