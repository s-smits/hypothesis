from typing import Any, cast

import pytest
from pydantic import ValidationError

from node_dag.dag import DagOutput
from node_dag.nodes.base import BaseScoreConfig
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.plan import (
    Assertion,
    Criterion,
    Critique,
    Plan,
    ToolRequest,
    VerifyOpinion,
    accepted,
    holds,
)
from node_dag.types import Dna, Table

D = Dna(sequence="ATGTAA")
CRIT = [Criterion(id="no_tcg", claim="no TCG remains")]
OK = VerifyOpinion(agrees=True, covers_goal=True, reason="r")


def _plan(**kw) -> Plan:
    step = {
        "node": "recode_targeted",
        "config": {"targeted_codons": ["TCG"]},
        "inputs": {"sequence": "seqs"},
        "why": "w",
    }
    ask = {"criterion": "no_tcg", "step": "none", "branch": "yes", "claim": "c"}
    base = {
        "hypothesis": "h",
        "expected": "e",
        "inputs": {"seqs": "dna"},
        "steps": {"fixed": step},
        "assertions": [ask],
    }
    return Plan.model_validate({**base, **kw})


def _out(yes: int, no: int) -> DagOutput:
    return DagOutput(
        values={"none.yes": Table.of([D] * yes), "none.no": Table.of([D] * no)},
        skipped=[],
    )


def test_accepted_says_why_not_for_each_reason():
    assert not accepted(CRIT, None, OK, _out(1, 0))[0]
    assert "no criteria" in accepted([], _plan(), OK, _out(1, 0))[1]
    assert (
        "no assertion covers" in accepted(CRIT, _plan(assertions=[]), OK, _out(1, 0))[1]
    )
    other = Criterion(id="keep_protein", claim="protein unchanged")
    assert "keep_protein" in accepted([*CRIT, other], _plan(), OK, _out(1, 0))[1]
    assert "did not hold" in accepted(CRIT, _plan(), OK, _out(1, 1))[1]
    assert (
        "cover"
        in accepted(
            CRIT, _plan(), OK.model_copy(update={"covers_goal": False}), _out(1, 0)
        )[1]
    )
    assert (
        "agree"
        in accepted(CRIT, _plan(), OK.model_copy(update={"agrees": False}), _out(1, 0))[
            1
        ]
    )


def test_accepted_when_every_criterion_is_covered_and_held():
    assert accepted(CRIT, _plan(), OK, _out(2, 0))[0]


def test_holds_needs_the_branch_full_and_the_other_empty():
    a = [Assertion(criterion="c", step="none", branch="yes", claim="c")]
    assert holds(a, None) == {"none.yes": False}
    assert holds(
        a, DagOutput(values={"none.yes": Table(), "none.no": Table()}, skipped=["none"])
    ) == {"none.yes": False}
    assert holds(a, _out(2, 0)) == {"none.yes": True}
    assert holds(a, _out(1, 1)) == {"none.yes": False}


def test_a_produced_assertion_holds_when_its_step_gave_an_entity():
    a = [Assertion(criterion="c", step="scored", branch="produced", claim="c")]
    gave = DagOutput(values={"scored": Table.of([D])}, skipped=[])
    assert holds(a, gave) == {"scored.produced": True}
    assert holds(a, DagOutput(values={"scored": Table()}, skipped=[])) == {
        "scored.produced": False
    }
    assert holds(a, None) == {"scored.produced": False}


def test_a_produced_assertion_on_a_filter_holds_when_it_kept_some_and_dropped_others():
    a = [Assertion(criterion="c", step="none", branch="produced", claim="c")]
    assert holds(a, _out(2, 0)) == {"none.produced": True}
    assert holds(a, _out(1, 3)) == {"none.produced": True}
    assert holds(a, _out(0, 2)) == {"none.produced": False}
    assert holds(a, None) == {"none.produced": False}


def test_a_criterion_says_who_wrote_it_and_older_files_read_as_human():
    assert Criterion(id="no_tcg", claim="no TCG remains").source == "human"
    assert Criterion.model_validate_json('{"id": "x", "claim": "c"}').source == "human"


def test_fingerprint_ignores_prose_not_wiring():
    base = _plan().fingerprint()
    assert _plan(hypothesis="reworded", expected="x").fingerprint() == base
    other = {
        "node": "recode_targeted",
        "config": {"targeted_codons": ["TCA"]},
        "inputs": {"sequence": "seqs"},
        "why": "w",
    }
    assert _plan(steps={"fixed": other}).fingerprint() != base


def test_request_with_unknown_kind_is_rejected():
    with pytest.raises(ValidationError, match="Unknown kind"):
        ToolRequest(
            name="x",
            node="tool",
            purpose="p",
            kind="plasmid",
            output="dna",
            why_needed="w",
            why_not_composable="w",
            example="e",
        )


def _step(node: str, source: str, port: str = "sequence") -> dict:
    return {"node": node, "inputs": {port: source}, "why": "w"}


def test_stand_ins_typecheck_a_plan_whose_nodes_do_not_exist():
    ask: dict[str, Any] = {
        "purpose": "p",
        "why_needed": "w",
        "why_not_composable": "w",
        "example": "e",
        "kind": "dna",
    }
    count = ToolRequest(name="count_gc", node="score", output=["gc"], **ask)
    to_aa = ToolRequest(name="to_aa", node="tool", output="amino_acid_sequence", **ask)
    gc, aa = cast("BaseScoreConfig", count.stand_in()()), to_aa.stand_in()()
    small = AtMostConfig(column=gc.columns()["gc"], threshold=0)
    ok = _plan(
        requests={"count_gc": count},
        steps={
            "counted": _step("count_gc", "seqs"),
            "small": _step("at_most", "counted", "items"),
        },
    )
    ok.typecheck({"counted": gc, "small": small})
    bad = _plan(
        requests={"count_gc": count, "to_aa": to_aa},
        steps={"prot": _step("to_aa", "seqs"), "counted": _step("count_gc", "prot")},
    )
    with pytest.raises(
        ValueError,
        match="'counted' port 'sequence' takes Dna, but 'prot' gives AminoAcidSequence",
    ):
        bad.typecheck({"prot": aa, "counted": gc})


def test_accepted_refuses_criteria_that_share_an_id():
    twin = Criterion(id="no_tcg", claim="no TCA remains either")
    ok, why = accepted([*CRIT, twin], _plan(), OK, _out(1, 0))
    assert not ok and "no_tcg" in why


@pytest.mark.parametrize("output", [Plan, list[Criterion], Critique, VerifyOpinion])
def test_every_agent_output_schema_is_one_anthropic_accepts(output):
    """The SDK rejects a property with no type before sending; an ``Any`` field is one."""
    from anthropic.lib._parse._transform import transform_schema
    from pydantic import TypeAdapter

    transform_schema(TypeAdapter(output).json_schema())


def test_a_config_field_default_keeps_its_type():
    from node_dag.plan import ConfigField

    assert ConfigField(name="n", type="int", description="d", default=3).default == 3
    assert ConfigField(
        name="s", type="list[str]", description="d", default=["TAA"]
    ).default == ["TAA"]
