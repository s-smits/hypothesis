"""The rule that stops the loop talking itself into success.

A run is achieved only when every frozen Criterion is covered by an Assertion whose
decision branch actually fired. The verifier may veto that; it can never grant it. None of
these tests touch a model, which is the point.
"""

import pytest
from pydantic import ValidationError

from node_dag.dag import DagOutput
from node_dag.plan import (
    Assertion,
    Criterion,
    Plan,
    PlannedStep,
    Verdict,
    accepted,
    holds,
)
from node_dag.types import Dna

RECODED = Dna(sequence="ATGAGCTAA")

NO_TCG = Criterion(id="no_tcg", claim="no TCG codon remains")
SAME_PROTEIN = Criterion(id="same_protein", claim="the protein is unchanged")


def _plan(assertions: list[Assertion]) -> Plan:
    return Plan(
        hypothesis="recode the sequence and check what is left",
        expected="no TCG remains and the protein is unchanged",
        assertions=assertions,
        inputs={"seq": "dna"},
        steps={
            "recoded": PlannedStep(
                node="recode_codons",
                config={"targets": ["TCG"]},
                inputs={"sequence": "seq"},
                why="swap the target codons for synonyms",
            ),
            "clean": PlannedStep(
                node="codons_absent",
                config={"codons": ["TCG"]},
                inputs={"sequence": "recoded"},
                why="prove no TCG survived the recoding",
            ),
        },
    )


def _outcome(*sources: str) -> DagOutput:
    return DagOutput(values={s: RECODED for s in sources}, skipped=[])


ALL_COVERED = [
    Assertion(criterion="no_tcg", step="clean", branch="yes", claim="no TCG remains"),
    Assertion(
        criterion="same_protein", step="clean", branch="yes", claim="protein kept"
    ),
]


def test_holds_is_true_only_when_the_branch_fired():
    a = Assertion(criterion="no_tcg", step="clean", branch="yes", claim="no TCG left")
    assert holds([a], _outcome("clean.yes")) == {"clean.yes": True}
    assert holds([a], _outcome("clean.no")) == {"clean.yes": False}
    assert holds([a], None) == {"clean.yes": False}


def test_a_produced_assertion_holds_when_the_step_returned_a_value():
    assertion = Assertion(
        criterion="score_returned",
        step="scored",
        branch="produced",
        claim="the scorer returned a value",
    )
    assert holds([assertion], _outcome("scored")) == {"scored": True}
    assert holds([assertion], _outcome()) == {"scored": False}


def test_a_covered_plan_whose_assertions_held_is_accepted():
    ok, why = accepted(
        [NO_TCG, SAME_PROTEIN],
        _plan(ALL_COVERED),
        Verdict(agrees=True, reason="matches", score=1.0),
        _outcome("clean.yes"),
    )
    assert ok, why


def test_a_plan_that_asserts_nothing_is_never_accepted():
    """The verifier liking it is not enough. This is the whole point of the rule."""
    ok, why = accepted(
        [NO_TCG],
        _plan([]),
        Verdict(agrees=True, reason="looks right to me", score=1.0),
        _outcome("clean.yes"),
    )
    assert not ok
    assert "asserted nothing" in why


def test_a_plan_that_covers_only_some_criteria_is_not_accepted():
    """Gap H: the builder cannot pass by writing itself an easier exam."""
    only_one = [a for a in ALL_COVERED if a.criterion == "no_tcg"]
    ok, why = accepted(
        [NO_TCG, SAME_PROTEIN],
        _plan(only_one),
        Verdict(agrees=True, reason="matches", score=1.0),
        _outcome("clean.yes"),
    )
    assert not ok
    assert "same_protein" in why


def test_a_failed_assertion_is_not_accepted():
    ok, why = accepted(
        [NO_TCG, SAME_PROTEIN],
        _plan(ALL_COVERED),
        Verdict(agrees=True, reason="matches", score=1.0),
        _outcome("clean.no"),
    )
    assert not ok
    assert "did not hold" in why


@pytest.mark.parametrize(
    ("verdict", "fragment"),
    [
        (Verdict(agrees=False, reason="the output is wrong"), "did not agree"),
        (
            Verdict(agrees=True, covers_goal=False, reason="checks are superficial"),
            "cover the goal",
        ),
    ],
)
def test_the_verifier_can_veto_a_plan_whose_assertions_held(verdict, fragment):
    ok, why = accepted(
        [NO_TCG, SAME_PROTEIN], _plan(ALL_COVERED), verdict, _outcome("clean.yes")
    )
    assert not ok
    assert fragment in why


def test_a_hypothesis_without_criteria_cannot_be_accepted():
    ok, why = accepted(
        [],
        _plan(ALL_COVERED),
        Verdict(agrees=True, reason="fine", score=1.0),
        _outcome("clean.yes"),
    )
    assert not ok
    assert "no criteria" in why


def test_a_round_without_a_verdict_is_not_accepted():
    assert not accepted([NO_TCG], _plan(ALL_COVERED), None, _outcome("clean.yes"))[0]
    assert not accepted([NO_TCG], None, Verdict(agrees=True, reason="x"), None)[0]


def test_a_verdict_defaults_to_not_achieved():
    """Every field a model could lie with defaults to the cautious answer."""
    v = Verdict(reason="no opinion")
    assert not v.agrees and not v.achieved and not v.prediction_held
    assert v.score == 0.0


def test_a_criterion_id_must_be_a_slug():
    with pytest.raises(ValidationError):
        Criterion(id="No TCG", claim="no TCG codon remains")
