from typing import ClassVar, Literal

from node_dag.nodes.base import BaseDecisionConfig, Category
from node_dag.types import FooBar


class AtLeastConfig(BaseDecisionConfig):
    """Filter: forward ``value`` on yes when its count is at least ``threshold``, else on no.

    Args:
        threshold: The lowest count that takes the yes branch.
    """

    name: Literal["at_least"] = "at_least"
    threshold: int
    categories = (Category.FILTER,)
    inputs: ClassVar = {"value": FooBar}
    forwards = "value"
