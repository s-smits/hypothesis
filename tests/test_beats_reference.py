"""The beats_reference filter: keep what scores better than a reference entity, measured."""

import json
import math

import pytest
from pydantic import ValidationError
from temporalio.client import WorkflowFailureError

from node_dag.dag import Dag
from node_dag.nodes.filters.beats_reference.config import BeatsReferenceConfig
from node_dag.nodes.filters.beats_reference.function import BeatsReference
from node_dag.types import Dna, Entity
from tests.test_dag import ACTIVITIES, SCORE, SEQS, _run, _step  # noqa: F401

REF = Dna(sequence="ATGGCTCTGAAATAA")
COL = "dummy__0000__score"
ITEMS: list[Entity] = [Dna(sequence="ATGGCTCTGAAATAA")] * 4


def keep(values: list[float], reference: float = 2.0, higher: bool = True):
    config = BeatsReferenceConfig(
        column=COL, reference=REF, scored_in="baseline", higher=higher
    )
    return BeatsReference(config).run(items=ITEMS, values=values, reference=reference)


def test_keeps_only_what_is_strictly_better_than_the_reference():
    assert keep([1.0, 2.0, 3.0, 4.0]) == [False, False, True, True]
    assert keep([1.0, 2.0, 3.0, 4.0], higher=False) == [True, False, False, False]


def test_a_tie_with_the_reference_is_not_better():
    assert keep([2.0, 2.0, 2.0, 2.0]) == [False] * 4


def test_a_non_finite_score_is_never_kept():
    assert keep([math.inf, math.nan, -math.inf, 3.0]) == [False, False, False, True]
    assert keep([math.inf, math.nan, -math.inf, 1.0], higher=False) == [False] * 3 + [
        True
    ]


def test_the_reference_is_part_of_the_config_hash_and_nothing_else_is_accepted():
    a = BeatsReferenceConfig(column=COL, reference=REF, scored_in="baseline")
    b = BeatsReferenceConfig(
        column=COL, reference=Dna(sequence="ATGGCCCTGAAATAA"), scored_in="baseline"
    )
    assert a.config_hash != b.config_hash
    with pytest.raises(ValidationError):
        BeatsReferenceConfig.model_validate(
            {**a.model_dump(), "config_hash": "", "threshold": 1}
        )


EXPRESSION = SCORE.columns()["expression"]


def _dag(**filter_fields) -> dict:
    """Score the inputs in step ``baseline`` and the variants in step ``scored``."""
    return {
        "inputs": {"seqs": "dna"},
        "steps": {
            "mutated": _step(
                {"name": "mutate_synonymous", "seed": 3, "count": 2}, "seqs"
            ),
            "scored": _step(SCORE.model_dump(mode="json"), "mutated"),
            "baseline": _step(SCORE.model_dump(mode="json"), "seqs"),
            "better": _step(
                {
                    "name": "beats_reference",
                    "column": EXPRESSION,
                    "reference": REF.model_dump(mode="json"),
                    "scored_in": "baseline",
                    **filter_fields,
                },
                "scored",
                "items",
            ),
        },
    }


async def test_a_run_keeps_what_beats_the_references_own_measured_score(results_dir):
    out = await _run(_dag(), SEQS)
    base = out.values["baseline"].scores[EXPRESSION][REF.id]
    scored = out.values["scored"].scores[EXPRESSION]
    yes, no = out.values["better.yes"], out.values["better.no"]
    assert yes.items and no.items
    assert all(scored[i.id] > base for i in yes.items)
    assert all(not scored[i.id] > base for i in no.items)
    assert len(yes.items) + len(no.items) == len(out.values["scored"].items)


async def test_a_reference_that_was_never_scored_fails_the_run_with_the_fix(
    results_dir,
):
    dag = _dag(reference=Dna(sequence="ATGGCTCTGAAGTAA").model_dump(mode="json"))
    with pytest.raises(WorkflowFailureError) as e:
        await _run(dag, SEQS)
    assert "Score the reference" in str(e.value.cause)


def test_the_scoring_step_runs_before_the_filter_and_must_hold_the_column():
    dag = Dag.model_validate(_dag())
    assert "baseline" in dag.steps["better"].deps()
    wrong = json.loads(json.dumps(_dag()))
    wrong["steps"]["baseline"] = _step({"name": "dna_to_protein"}, "seqs")
    with pytest.raises(ValidationError, match="not 'ostir_expression"):
        Dag.model_validate(wrong)
    wrong["steps"]["better"]["config"]["scored_in"] = "nowhere"
    with pytest.raises(ValidationError, match="Unknown source"):
        Dag.model_validate(wrong)
