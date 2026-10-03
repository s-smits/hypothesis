"""The top_k filter: rank-based selection, deterministic ties, non-finite scores."""

import math

import pytest
from pydantic import ValidationError

from node_dag.nodes.filters.top_k.config import TopKConfig
from node_dag.nodes.filters.top_k.function import TopK
from node_dag.types import Dna, Entity

# Annotated as the port's declared type, which is what run takes.
SEQS: list[Entity] = [
    Dna(sequence="ATGGCTCTGAAATAA"),
    Dna(sequence="ATGGCGCTGAAATAG"),
    Dna(sequence="ATGGCCCTGAAGTAG"),
    Dna(sequence="ATGGCGTTGAAATGA"),
]
COL = "dummy__0000__score"


def keep(
    values: list[float],
    k: int,
    largest: bool = True,
    items: list[Entity] | None = None,
) -> list[bool]:
    node = TopK(TopKConfig(column=COL, k=k, largest=largest))
    return node.run(items=SEQS if items is None else items, values=values)


def test_keeps_the_k_highest():
    assert keep([1.0, 4.0, 2.0, 3.0], k=2) == [False, True, False, True]


def test_keeps_the_k_lowest_when_largest_is_false():
    assert keep([1.0, 4.0, 2.0, 3.0], k=2, largest=False) == [True, False, True, False]


def test_k_of_one_takes_the_single_best():
    assert keep([1.0, 4.0, 2.0, 3.0], k=1) == [False, True, False, False]
    assert keep([1.0, 4.0, 2.0, 3.0], k=1, largest=False) == [
        True,
        False,
        False,
        False,
    ]


def test_one_bool_per_input_aligned_with_values():
    values = [1.0, 4.0, 2.0, 3.0]
    out = keep(values, k=2)
    assert len(out) == len(values) == len(SEQS)


def test_k_larger_than_the_pool_keeps_everything():
    assert keep([1.0, 2.0, 3.0, 4.0], k=99) == [True] * 4


def test_k_equal_to_the_pool_keeps_everything():
    assert keep([1.0, 2.0, 3.0, 4.0], k=4) == [True] * 4


def test_empty_input_gives_empty_output():
    node = TopK(TopKConfig(column=COL, k=2))
    assert node.run(items=[], values=[]) == []


def test_ties_broken_by_id_not_by_input_order():
    """The same entities in a different order must select the same entities."""
    values = [5.0, 5.0, 5.0, 5.0]
    forward = [s for s, k in zip(SEQS, keep(values, k=2)) if k]
    reversed_seqs: list[Entity] = list(reversed(SEQS))
    node = TopK(TopKConfig(column=COL, k=2))
    back = [
        s
        for s, k in zip(reversed_seqs, node.run(items=reversed_seqs, values=values))
        if k
    ]
    assert {s.id for s in forward} == {s.id for s in back}


def test_tie_selection_is_the_lowest_ids():
    values = [5.0, 5.0, 5.0, 5.0]
    chosen = {s.id for s, k in zip(SEQS, keep(values, k=2)) if k}
    assert chosen == set(sorted(s.id for s in SEQS)[:2])


def test_partial_tie_at_the_cut_is_deterministic():
    # Two entities tie on the second-best value; exactly one must be kept.
    values = [9.0, 5.0, 5.0, 1.0]
    out = keep(values, k=2)
    assert out[0] is True
    assert out[3] is False
    assert sum(out) == 2
    assert out == keep(values, k=2)  # repeatable


def test_nan_is_never_selected():
    out = keep([float("nan"), 1.0, 2.0, 3.0], k=3)
    assert out[0] is False
    assert sum(out) == 3


def test_nan_does_not_consume_a_slot():
    """A NaN must not crowd out a real candidate."""
    out = keep([float("nan"), 1.0, 2.0, 3.0], k=2)
    assert out == [False, False, True, True]


def test_infinity_is_never_selected_even_though_it_ranks_highest():
    out = keep([float("inf"), 1.0, 2.0, 3.0], k=2)
    assert out[0] is False
    assert out == [False, False, True, True]


def test_negative_infinity_is_never_selected_when_keeping_lowest():
    out = keep([float("-inf"), 1.0, 2.0, 3.0], k=2, largest=False)
    assert out[0] is False
    assert out == [False, True, True, False]


def test_all_non_finite_keeps_nothing():
    out = keep([float("nan"), float("inf"), float("-inf"), float("nan")], k=2)
    assert out == [False] * 4


def test_fewer_finite_than_k_keeps_only_the_finite():
    out = keep([float("nan"), float("nan"), 2.0, 3.0], k=3)
    assert out == [False, False, True, True]


def test_negative_and_zero_values_rank_normally():
    assert keep([-5.0, 0.0, -1.0, 3.0], k=2) == [False, True, False, True]
    assert keep([-5.0, 0.0, -1.0, 3.0], k=2, largest=False) == [
        True,
        False,
        True,
        False,
    ]


@pytest.mark.parametrize("k", [0, -1])
def test_k_must_be_at_least_one(k: int):
    with pytest.raises(ValidationError):
        TopKConfig(column=COL, k=k)


def test_config_rejects_extra_fields():
    with pytest.raises(ValidationError):
        TopKConfig.model_validate({"column": COL, "k": 1, "nonsense": 2})


def test_k_and_largest_change_the_config_hash():
    base = TopKConfig(column=COL, k=2)
    assert base.config_hash != TopKConfig(column=COL, k=3).config_hash
    assert base.config_hash != TopKConfig(column=COL, k=2, largest=False).config_hash
    assert base.config_hash != TopKConfig(column="other__0000__s", k=2).config_hash


def test_selection_is_independent_of_pool_order_for_distinct_values():
    values = [1.0, 4.0, 2.0, 3.0]
    chosen = {s.id for s, k in zip(SEQS, keep(values, k=2)) if k}
    pairs = list(zip(SEQS, values))[::-1]
    node = TopK(TopKConfig(column=COL, k=2))
    items: list[Entity] = [s for s, _ in pairs]
    out = node.run(items=items, values=[v for _, v in pairs])
    assert {s.id for s, k in zip(items, out) if k} == chosen


def test_finite_check_matches_math_isfinite():
    """The documented rule is exactly math.isfinite, not a hand-rolled bound."""
    values = [1e308, float("inf"), -1e308, float("nan")]
    out = keep(values, k=4)
    assert out == [math.isfinite(v) for v in values]
