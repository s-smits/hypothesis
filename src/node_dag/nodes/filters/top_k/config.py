from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseFilterConfig, Category
from node_dag.types import Entity


class TopKConfig(BaseFilterConfig):
    """Keep the ``k`` entities with the best score in ``column``, and no more.

    Unlike ``at_least`` and ``at_most``, which need a threshold chosen in advance, this
    selects by rank. It is what a best-of-N comparison needs: generate a pool, score it,
    and keep the best few whatever their absolute values turn out to be.

    Rank is taken over the whole list that reaches the step, so the result depends on
    that list. Two runs over different pools select different entities even at the same
    ``k``; a benchmark should therefore fix the pool, not just the configuration.

    Ties are broken by entity id, so the selection does not depend on the order the
    entities arrive in. Non-finite scores are never selected: a NaN from a failed
    scorer, or an infinity, goes to the no branch however ``largest`` is set, so a
    broken score cannot win a comparison. They are still counted in the input, so the
    no branch shows them rather than hiding them.

    Fewer than ``k`` entities with finite scores keeps all of them. It is not an error,
    so check the yes branch's size rather than assuming ``k`` entities came out.

    Args:
        column: The score column to rank on, ``<node name>__<config hash>__<score name>``.
        k: How many entities to keep.
        largest: Keep the highest scores. Set it False to keep the lowest.
    """

    name: Literal["top_k"] = "top_k"
    k: int = Field(ge=1)
    largest: bool = True
    categories = (Category.FILTER,)
    inputs: ClassVar = {"items": Entity}
    intents: ClassVar = (
        "keep the best k entities by score",
        "select the top or bottom candidates by rank, without a threshold",
        "take the single best candidate from a pool",
        "best-of-N selection over generated variants",
    )
    when_to_use: ClassVar = (
        "Use to select the best few candidates from a scored pool when no sensible "
        "absolute threshold is known, or to take the single best with k=1."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal names an absolute cutoff a candidate must meet; "
        "at_least and at_most do that. Do not use to combine two score columns."
    )
