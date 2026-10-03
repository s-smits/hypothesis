from typing import ClassVar, Literal

from node_dag.nodes.base import BaseDecisionConfig, Category
from node_dag.types import Score


class AtLeastConfig(BaseDecisionConfig):
    """Filter: forward ``value`` on yes when its score is at least ``threshold``, else on no.

    Args:
        threshold: The lowest score value that takes the yes branch.
    """

    name: Literal["at_least"] = "at_least"
    threshold: float
    categories = (Category.FILTER,)
    inputs: ClassVar = {"value": Score}
    forwards = "value"
