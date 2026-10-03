from typing import ClassVar, Literal

from node_dag.nodes.base import BaseFilterConfig, Category
from node_dag.types import Entity


class AtMostConfig(BaseFilterConfig):
    """Keep the entities whose score in ``column`` is at most ``threshold``.

    The rest go to the no branch.

    Args:
        column: The score column to filter on, ``<node name>__<config hash>__<score name>``.
        threshold: The highest score that is kept.
    """

    name: Literal["at_most"] = "at_most"
    threshold: float
    categories = (Category.FILTER,)
    inputs: ClassVar = {"items": Entity}
    intents: ClassVar = (
        "keep entities with score at most threshold",
        "filter for lower scores or values below a maximum cutoff",
        "select candidate sequences with reduced atom count",
        "filter to keep only synonymous sequences (amino_acid_changes at most 0)",
    )
    when_to_use: ClassVar = (
        "Use after a scoring node to select entities whose score is at or below a "
        "threshold (e.g. fewer atoms <= baseline - 1, or amino_acid_changes <= 0)."
    )
    when_not_to_use: ClassVar = (
        "Do not use when filtering for higher values (use at_least instead)."
    )
