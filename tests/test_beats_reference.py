"""The beats_reference filter: keep what scores better than a reference entity, measured."""

import json
import math

import pytest
from pydantic import ValidationError
from temporalio.client import WorkflowFailureError
from test_dag import ACTIVITIES, SCORE, SEQS, _run, _step  # noqa: F401

from node_dag.dag import Dag
from node_dag.nodes.filters.beats_reference.config import BeatsReferenceConfig
from node_dag.nodes.filters.beats_reference.function import BeatsReference
from node_dag.types import Dna, Entity

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


def test_a_scored_in_that_names_no_step_says_which_step_and_which_node():
    # A registered node keeps its plan-local scored_in, so a later plan may lack the step.
    with pytest.raises(ValidationError) as e:
        Dag.model_validate(_dag(scored_in="cai_in"))
    msg = str(e.value)
    assert "Unknown source 'cai_in'" in msg and "Step 'better'" in msg
    assert "scored_in" in msg and "beats_reference__" in msg


async def test_scored_in_may_name_a_filter_branch_and_that_filter_runs_first(
    results_dir,
):
    # The reference is scored in baseline, and passes a gate that keeps what expresses.
    dag = _dag()
    dag["steps"]["gate"] = _step(
        {"name": "at_least", "column": EXPRESSION, "threshold": 0}, "baseline", "items"
    )
    dag["steps"]["better"]["config"]["scored_in"] = "gate.yes"
    assert "gate" in Dag.model_validate(dag).steps["better"].deps()
    out = await _run(dag, SEQS)
    base = out.values["gate.yes"].scores[EXPRESSION][REF.id]
    assert out.values["better.yes"].items
    assert all(
        out.values["scored"].scores[EXPRESSION][i.id] > base
        for i in out.values["better.yes"].items
    )


async def test_a_reference_that_was_never_scored_is_not_retried(results_dir):
    dag = _dag(reference=Dna(sequence="ATGGCTCTGAAGTAA").model_dump(mode="json"))
    with pytest.raises(WorkflowFailureError) as e:
        await _run(dag, SEQS)
    assert getattr(e.value.cause, "non_retryable", False)


async def test_a_scored_in_past_a_tool_says_which_input_holds_the_reference(
    results_dir,
):
    # The reference is an input, but this step scores the tool's output, not the input.
    with pytest.raises(WorkflowFailureError) as e:
        await _run(_dag(scored_in="scored"), SEQS)
    msg = str(e.value.cause)
    assert "ATGGCTCTGAAATAA" in msg and REF.id in msg  # What it typed, and its id.
    assert "in input ['seqs']" in msg and "'scored' reads" in msg
    assert "output of a tool" in msg and "Score the reference" in msg


async def test_a_scored_in_branch_that_lost_the_reference_says_a_filter_dropped_it(
    results_dir,
):
    # The step reads the input that holds the reference, but its filter kept nothing.
    dag = _dag()
    dag["steps"]["gate"] = _step(
        {"name": "at_least", "column": EXPRESSION, "threshold": 1e18},
        "baseline",
        "items",
    )
    dag["steps"]["better"]["config"]["scored_in"] = "gate.yes"
    with pytest.raises(WorkflowFailureError) as e:
        await _run(dag, SEQS)
    msg = str(e.value.cause)
    assert "'gate.yes' reads input ['seqs'], which holds the reference" in msg
    assert "was not kept by a filter on the way" in msg and "Score the reference" in msg
