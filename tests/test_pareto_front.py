"""The pareto_front filter, and the multi-column filter plumbing it needs."""

import pytest
from pydantic import ValidationError

from node_dag.dag import Dag
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.nodes.filters.pareto_front.config import ParetoFrontConfig
from node_dag.nodes.filters.pareto_front.function import ParetoFront
from node_dag.types import Dna, Entity

A, B = "sc__aaaa__a", "sc__aaaa__b"
C = "sc__aaaa__c"


def items(n: int) -> list[Entity]:
    # Distinct sequences, so ids differ and nothing merges.
    return [Dna(sequence="ATG" + "AAA" * i + "TAA") for i in range(1, n + 1)]


def front(objectives: dict[str, bool], values: dict[str, list[float]]) -> list[bool]:
    cfg = ParetoFrontConfig(objectives=objectives)
    n = len(next(iter(values.values())))
    return ParetoFront(cfg).run(items=items(n), values=values)


# --- dominance ---------------------------------------------------------------


def test_dominated_point_is_dropped():
    # b is worse than a on both objectives.
    out = front({A: True, B: True}, {A: [2.0, 1.0], B: [2.0, 1.0]})
    assert out == [True, False]


def test_trade_off_keeps_both():
    out = front({A: True, B: True}, {A: [2.0, 1.0], B: [1.0, 2.0]})
    assert out == [True, True]


def test_equal_points_both_stay():
    """Equal points do not dominate each other, so neither displaces the other."""
    out = front({A: True, B: True}, {A: [1.0, 1.0], B: [2.0, 2.0]})
    assert out == [True, True]


def test_at_least_as_good_everywhere_and_better_somewhere_dominates():
    # a ties b on A and beats it on B.
    out = front({A: True, B: True}, {A: [1.0, 1.0], B: [2.0, 1.0]})
    assert out == [True, False]


def test_minimising_objective_is_respected():
    # Lower B is better, so the second point wins on B and the first on A.
    out = front({A: True, B: False}, {A: [2.0, 1.0], B: [2.0, 1.0]})
    assert out == [True, True]
    # Now the first point is better on both, A higher and B lower.
    out = front({A: True, B: False}, {A: [2.0, 1.0], B: [1.0, 2.0]})
    assert out == [True, False]


def test_all_minimising():
    out = front({A: False, B: False}, {A: [1.0, 2.0], B: [1.0, 2.0]})
    assert out == [True, False]


def test_three_objectives():
    out = front(
        {A: True, B: True, C: True},
        {A: [1.0, 2.0, 0.0], B: [2.0, 1.0, 0.0], C: [1.0, 1.0, 0.0]},
    )
    assert out == [True, True, False]


def test_a_single_best_point_dominates_everything():
    out = front(
        {A: True, B: True},
        {A: [9.0, 1.0, 2.0, 3.0], B: [9.0, 1.0, 2.0, 3.0]},
    )
    assert out == [True, False, False, False]


def test_front_can_be_the_whole_pool():
    out = front(
        {A: True, B: True},
        {A: [1.0, 2.0, 3.0], B: [3.0, 2.0, 1.0]},
    )
    assert out == [True, True, True]


def test_one_bool_per_entity():
    values = {A: [1.0, 2.0, 3.0], B: [3.0, 2.0, 1.0]}
    assert len(front({A: True, B: True}, values)) == 3


def test_empty_input():
    cfg = ParetoFrontConfig(objectives={A: True, B: True})
    assert ParetoFront(cfg).run(items=[], values={A: [], B: []}) == []


def test_order_of_the_pool_does_not_change_who_survives():
    values = {A: [2.0, 1.0, 1.5], B: [1.0, 2.0, 1.4]}
    keep = front({A: True, B: True}, values)
    reversed_values = {A: values[A][::-1], B: values[B][::-1]}
    rev = front({A: True, B: True}, reversed_values)
    assert keep == list(reversed(rev))


# --- non-finite scores -------------------------------------------------------


def test_nan_in_any_objective_is_dropped():
    out = front({A: True, B: True}, {A: [float("nan"), 1.0], B: [9.0, 1.0]})
    assert out == [False, True]


def test_infinity_cannot_sit_on_the_front():
    out = front({A: True, B: True}, {A: [float("inf"), 1.0], B: [1.0, 1.0]})
    assert out == [False, True]


def test_all_non_finite_keeps_nothing():
    out = front({A: True, B: True}, {A: [float("nan"), float("inf")], B: [1.0, 2.0]})
    assert out == [False, False]


def test_a_non_finite_point_does_not_dominate_a_finite_one():
    out = front({A: True, B: True}, {A: [float("inf"), 1.0], B: [float("inf"), 1.0]})
    assert out == [False, True]


# --- config ------------------------------------------------------------------


def test_column_is_set_from_the_first_objective():
    cfg = ParetoFrontConfig(objectives={A: True, B: False})
    assert cfg.column == A
    assert cfg.score_columns() == (A, B)


def test_explicit_matching_column_is_accepted():
    cfg = ParetoFrontConfig.model_validate(
        {"objectives": {A: True, B: True}, "column": A}
    )
    assert cfg.column == A


def test_explicit_mismatched_column_is_rejected():
    with pytest.raises(ValidationError, match="not the first objective"):
        ParetoFrontConfig.model_validate(
            {"objectives": {A: True, B: True}, "column": B}
        )


@pytest.mark.parametrize("objectives", [{}, {A: True}])
def test_fewer_than_two_objectives_is_rejected(objectives: dict[str, bool]):
    with pytest.raises(ValidationError):
        ParetoFrontConfig(objectives=objectives)


def test_objectives_change_the_config_hash():
    base = ParetoFrontConfig(objectives={A: True, B: True})
    assert (
        base.config_hash
        != ParetoFrontConfig(objectives={A: True, B: False}).config_hash
    )
    assert (
        base.config_hash != ParetoFrontConfig(objectives={A: True, C: True}).config_hash
    )


def test_declares_the_dict_values_type():
    assert ParetoFrontConfig.values_type == dict[str, list[float]]
    assert AtLeastConfig.values_type == list[float]


# --- plumbing ----------------------------------------------------------------


def test_single_column_filters_still_report_one_column():
    cfg = AtLeastConfig(column=A, threshold=1.0)
    assert cfg.score_columns() == (A,)


def test_dag_rejects_a_pareto_objective_nothing_upstream_makes():
    score = {
        "name": "codon_adaptation",
        "codon_weights": {"ATG": 1.0, "TAA": 1.0, "AAA": 1.0},
    }
    dag = {
        "inputs": {"seqs": "dna"},
        "steps": {
            "scored": {"config": score, "inputs": {"sequence": "seqs"}},
            "best": {
                "config": {
                    "name": "pareto_front",
                    "objectives": {"missing__0000__x": True, "also__0000__y": True},
                },
                "inputs": {"items": "scored"},
            },
        },
    }
    with pytest.raises(ValidationError, match="filters on"):
        Dag.model_validate(dag)


def test_dag_accepts_pareto_on_two_real_columns():
    from node_dag.nodes.tools.codon_adaptation.config import CodonAdaptationConfig
    from node_dag.nodes.tools.gc_content.config import GcContentConfig

    cai = CodonAdaptationConfig(codon_weights={"ATG": 1.0, "AAA": 0.5, "TAA": 1.0})
    gc = GcContentConfig(window=9)
    dag = Dag.model_validate(
        {
            "inputs": {"seqs": "dna"},
            "steps": {
                "cai": {
                    "config": cai.model_dump(exclude={"config_hash"}),
                    "inputs": {"sequence": "seqs"},
                },
                "gc": {
                    "config": gc.model_dump(exclude={"config_hash"}),
                    "inputs": {"sequence": "cai"},
                },
                "front": {
                    "config": {
                        "name": "pareto_front",
                        "objectives": {
                            cai.columns()["cai"]: True,
                            next(iter(gc.columns().values())): False,
                        },
                    },
                    "inputs": {"items": "gc"},
                },
            },
        }
    )
    assert "front.yes" in [f"{k}.{b}" for k in dag.steps for b in ("yes", "no")]
