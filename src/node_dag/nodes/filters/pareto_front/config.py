from typing import Any, ClassVar, Literal

from pydantic import model_validator

from node_dag.nodes.base import BaseFilterConfig, Category
from node_dag.types import Entity


class ParetoFrontConfig(BaseFilterConfig):
    """Keep the entities no other entity beats on every objective at once.

    This is the multi-objective counterpart to ``at_least`` and ``top_k``, which each
    read one column. An entity is dropped only if some other entity is at least as
    good on every objective and strictly better on one, so what survives is the
    trade-off front: the candidates where improving one objective would cost another.

    It takes no weights and does no normalisation, which is the point. Objectives here
    have different units, such as a CAI between 0 and 1 against a folding energy in
    kcal/mol, and any single weighting of those is a scientific choice that belongs in
    the experiment rather than inside a filter. Dominance needs no common scale.

    The front is relative to the list that reaches the step, as rank is for ``top_k``:
    the same configuration over a different pool keeps different entities, so a
    benchmark has to fix the pool and not just the configuration. The front is also not
    a fixed size, and with many objectives most of a small pool can survive it. Rank
    the survivors with ``top_k`` on one objective to get a bounded number out.

    An entity whose score is non-finite in any objective is dropped, so a NaN from a
    failed scorer cannot sit on the front by being incomparable. It goes to the no
    branch, where it stays visible.

    Args:
        objectives: Each score column to weigh, mapped to whether higher is better:
            True to maximise it, False to minimise. At least two are needed; use
            ``at_least``, ``at_most`` or ``top_k`` for one. ``column`` is set from the
            first of these and need not be given.
    """

    name: Literal["pareto_front"] = "pareto_front"
    objectives: dict[str, bool]
    # Derived from objectives by the validator below, so a caller leaves it out. It is
    # still a field, so it is still part of config_hash and still the column Dag
    # reports first when an objective is missing upstream.
    column: str = ""
    categories = (Category.FILTER,)
    inputs: ClassVar = {"items": Entity}
    values_type: ClassVar[Any] = dict[str, list[float]]
    intents: ClassVar = (
        "keep the entities that trade off several objectives best",
        "select the Pareto front or non-dominated candidates",
        "filter on two or more score columns at once",
        "balance competing objectives without choosing weights",
    )
    when_to_use: ClassVar = (
        "Use when the goal names two or more objectives that pull against each other "
        "and no weighting between them has been decided."
    )
    when_not_to_use: ClassVar = (
        "Do not use for one objective; at_least, at_most and top_k do that. Do not "
        "use when a bounded number of candidates is needed, since the front's size "
        "depends on the pool."
    )

    @model_validator(mode="before")
    @classmethod
    def _set_column(cls, data: Any) -> Any:  # noqa: ANN401
        """Fill ``column`` from the first objective, so the base's field is satisfied."""
        if not isinstance(data, dict):
            return data
        objectives = data.get("objectives")
        if isinstance(objectives, dict) and objectives:
            first = next(iter(objectives))
            if data.get("column") not in (None, first):
                raise ValueError(
                    f"column {data['column']!r} is not the first objective {first!r}. "
                    "Leave column out; it is set from objectives."
                )
            return {**data, "column": first}
        return data

    @model_validator(mode="after")
    def _check_objectives(self) -> "ParetoFrontConfig":
        if len(self.objectives) < 2:
            raise ValueError(
                f"A Pareto front needs at least two objectives, got "
                f"{sorted(self.objectives)}. Use at_least, at_most or top_k for one."
            )
        return self

    def score_columns(self) -> tuple[str, ...]:
        """Every objective's column, in the order they were given."""
        return tuple(self.objectives)
