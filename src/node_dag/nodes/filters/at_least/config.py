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
    intents: ClassVar = (
        "keep entities with score at least threshold",
        "filter for higher scores or values above a minimum cutoff",
        "keep entities at or above a bar the goal states as a number",
    )
    when_to_use: ClassVar = (
        "Use after a scoring node when the goal states the bar as a number, such as "
        "expression of at least 40. When the bar is another entity's score, use "
        "beats_reference."
    )
    when_not_to_use: ClassVar = (
        "Do not set threshold to 0.0 or a trivial value that keeps all entities when "
        "the goal asks to select or find better ones."
    )
