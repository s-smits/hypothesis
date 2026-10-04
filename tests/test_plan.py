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
from node_dag.types import Dna, Table, Value

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


def test_two_criteria_on_one_step_and_branch_must_each_hold_to_accept():
    both = [*CRIT, Criterion(id="all_kept", claim="every sequence is kept")]
    ask = {"step": "none", "branch": "yes", "claim": "c"}
    plan = _plan(assertions=[{"criterion": c.id, **ask} for c in both])
    assert len(plan.assertions) == 2 and len(holds(plan.assertions, _out(2, 0))) == 1
    assert accepted(both, plan, OK, _out(2, 0))[0]
    assert "did not hold" in accepted(both, plan, OK, _out(1, 1))[1]
    one = _plan(assertions=[{"criterion": "no_tcg", **ask}])
    assert "all_kept" in accepted(both, one, OK, _out(2, 0))[1]


def _step(node: str, source: str, port: str = "sequence") -> dict:
    return {"node": node, "config": {}, "inputs": {port: source}, "why": "w"}


def _says(criterion: str, step: str, branch: str = "yes") -> dict:
    return {"criterion": criterion, "step": step, "branch": branch, "claim": "c"}


BOTH = [Criterion(id="higher", claim="above"), Criterion(id="protein", claim="same")]


def _pools(**kw) -> Plan:
    """Two separate candidate pools from one input, each with its own filter."""
    steps = {
        "gen_a": _step("mutate", "seqs"),
        "score_a": _step("cai", "gen_a"),
        "above": _step("at_least", "score_a", "items"),
        "gen_b": _step("mutate", "seqs"),
        "score_b": _step("constraint_check", "gen_b"),
        "same": _step("at_least", "score_b", "items"),
    }
    seen = [_says("higher", "above"), _says("protein", "same")]
    return _plan(steps=steps, assertions=seen, **kw)


def _pools_out() -> DagOutput:
    """Pool A is all above the baseline but changes the protein; pool B is the reverse."""
    other = Dna(sequence="ATGAAATAA")
    kept: dict[str, list[Value]] = {"above.yes": [D], "above.no": []}
    kept |= {"same.yes": [other], "same.no": []}
    return DagOutput(values={k: Table.of(v) for k, v in kept.items()}, skipped=[])


def test_proofs_on_two_separate_pools_are_accepted_unless_a_result_is_declared():
    assert accepted(BOTH, _pools(), OK, _pools_out())[0]
    for result, left in [("gen_a", "protein"), ("gen_b", "higher")]:
        ok, why = accepted(BOTH, _pools(result_source=result), OK, _pools_out())
        assert not ok and left in why and result in why


def test_a_declared_result_counts_its_own_step_and_the_steps_that_read_it():
    steps = {
        "pool": _step("mutate", "seqs"),
        "above": _step("at_least", "pool", "items"),
        "same": _step("at_least", "above.yes", "items"),
    }
    seen = [_says("higher", "above"), _says("protein", "same")]
    full = {k: Table.of([D]) for k in ("pool", "above.yes", "same.yes")}
    out = DagOutput(
        values={**full, "above.no": Table(), "same.no": Table()}, skipped=[]
    )
    for result in ("pool", "above.yes"):
        plan = _plan(steps=steps, assertions=seen, result_source=result)
        assert accepted(BOTH, plan, OK, out)[0], result
    # `above.no` is not what `same` reads: another set of entities.
    plan = _plan(steps=steps, assertions=seen, result_source="above.no")
    assert "protein" in accepted(BOTH, plan, OK, out)[1]


def test_a_declared_pool_counts_a_proof_on_what_its_filter_dropped_but_a_kept_set_does_not():
    """Saved accepts audit the dropped half too (arm-E 6148f02d r3, arm-F2 ce899c7c r2)."""
    steps = {
        "pool": _step("mutate", "seqs"),
        "above": _step("at_least", "pool", "items"),
        "audit": _step("at_most", "above.no", "items"),
    }
    seen = [_says("higher", "above", "produced"), _says("protein", "audit")]
    other = Dna(sequence="ATGAAATAA")
    kept: dict[str, list[Value]] = {"pool": [D, other], "above.yes": [D]}
    kept |= {"above.no": [other], "audit.yes": [other], "audit.no": []}
    out = DagOutput(values={k: Table.of(v) for k, v in kept.items()}, skipped=[])
    pool = _plan(steps=steps, assertions=seen, result_source="pool")
    assert accepted(BOTH, pool, OK, out)[0]
    only_kept = _plan(steps=steps, assertions=seen, result_source="above.yes")
    assert "protein" in accepted(BOTH, only_kept, OK, out)[1]


def test_a_declared_result_must_have_entities():
    plan = _plan(assertions=[_says("no_tcg", "none", "no")], result_source="none.yes")
    assert holds(plan.assertions, _out(0, 2)) == {"none.no": True}
    ok, why = accepted(CRIT, plan, OK, _out(0, 2))
    assert not ok and "none.yes" in why and "empty" in why
    assert accepted(CRIT, _plan(assertions=plan.assertions), OK, _out(0, 2))[0]


def test_acceptance_with_no_declared_result_is_unchanged():
    """Passes on the commit before ``result_source``: nothing is different until a plan sets it."""
    saved = _plan().model_dump()
    saved.pop("result_source", None)  # A file saved before the field existed.
    old = Plan.model_validate(saved)
    asked = [
        (
            CRIT,
            old,
            OK,
            _out(2, 0),
            "every criterion was covered by an assertion that held",
        ),
        (CRIT, _plan(assertions=[]), OK, _out(2, 0), "no assertion covers ['no_tcg']"),
        (CRIT, old, OK, _out(1, 1), "these assertions did not hold: ['none.yes']"),
    ]
    for criteria, plan, opinion, outcome, why in asked:
        assert accepted(criteria, plan, opinion, outcome)[1] == why
    # The wiring check that stops a repeat reads nothing about a result.
    assert old.fingerprint() == _plan(result_source="none.yes").fingerprint()


def test_the_builder_is_told_about_result_source_where_it_reads_the_plan():
    """Weak by nature: it shows the text is there, not that a model follows it."""
    from node_dag.agent import BUILD_INSTRUCTIONS

    said = Plan.model_json_schema()["properties"]["result_source"]
    assert "step output" in said["description"]
    assert BUILD_INSTRUCTIONS.count("result_source") == 1


def test_the_result_is_optional_in_the_plan_schema():
    schema = Plan.model_json_schema()
    assert schema["required"] == ["hypothesis", "expected", "inputs", "steps"]
    assert schema["properties"]["result_source"]["default"] is None
    assert Plan.model_validate(_plan()).on_result() is None


def test_holds_needs_the_branch_full_and_the_other_empty():
    a = [Assertion(criterion="c", step="none", branch="yes", claim="c")]
    assert holds(a, None) == {"none.yes": False}
    assert holds(
        a, DagOutput(values={"none.yes": Table(), "none.no": Table()}, skipped=["none"])
    ) == {"none.yes": False}
    assert holds(a, _out(2, 0)) == {"none.yes": True}
    assert holds(a, _out(1, 1)) == {"none.yes": False}


def test_holds_no_needs_its_branch_full_and_the_yes_branch_empty():
    a = [Assertion(criterion="c", step="none", branch="no", claim="c")]
    assert holds(a, _out(0, 2)) == {"none.no": True}
    assert holds(a, _out(1, 1)) == {"none.no": False}
    assert holds(a, _out(2, 0)) == {"none.no": False}
    assert holds(a, _out(0, 0)) == {"none.no": False}


def test_accepted_without_a_verifier_opinion_says_so_even_when_the_assertions_hold():
    ok, why = accepted(CRIT, _plan(), None, _out(2, 0))
    assert not ok and "no plan or verifier opinion" in why


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


def _says_on(step: str, branch: str, criterion: str = "no_tcg") -> dict:
    return {"criterion": criterion, "step": step, "branch": branch, "claim": "c"}


def test_a_plan_reruns_an_earlier_one_unless_it_keeps_every_assertion_and_strengthens_one():
    produced = _plan(
        assertions=[_says_on("f", "produced"), _says_on("g", "no", "other")]
    )
    yes = _plan(assertions=[_says_on("f", "yes"), _says_on("g", "no", "other")])
    extra = _plan(
        assertions=[*produced.assertions, _says_on("h", "produced", "third")],
    )
    assert yes.reruns(produced) is False  # produced -> yes, nothing dropped
    assert extra.reruns(produced) is False  # one more assertion that must hold
    assert produced.reruns(produced) is True  # the same
    assert produced.reruns(yes) is True  # weaker
    assert _plan(assertions=[_says_on("f", "yes")]).reruns(yes) is True  # dropped one
    assert _plan(assertions=[_says_on("h", "yes"), *yes.assertions[1:]]).reruns(yes)
    other = _plan(steps={"renamed": next(iter(produced.steps.values()))})
    assert other.reruns(produced) is False  # other wiring: not a repeat at all


def test_yes_implies_produced_and_no_implies_only_no():
    f = {
        s: Assertion(criterion="c", step="f", branch=s, claim="c")
        for s in ("yes", "no", "produced")
    }
    assert f["yes"].implies(f["produced"]) and f["yes"].implies(f["yes"])
    assert not f["produced"].implies(f["yes"])
    assert not f["no"].implies(f["produced"]) and not f["yes"].implies(f["no"])
    assert not f["yes"].implies(
        Assertion(criterion="c", step="g", branch="produced", claim="c")
    )


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


@pytest.mark.parametrize("confused", ["tool", "score", "filter"])
def test_a_request_that_puts_its_node_type_in_kind_is_told_what_kind_means(confused):
    """Two saved runs wrote kind='tool' or kind='filter'. The error must say what to write."""
    with pytest.raises(ValidationError) as e:
        ToolRequest(
            name="x",
            node="tool",
            purpose="p",
            kind=confused,
            output="dna",
            why_needed="w",
            why_not_composable="w",
            example="e",
        )
    text = str(e.value)
    assert "input port" in text and "'tool', 'score' or 'filter'" in text


def test_a_request_with_a_bad_output_says_what_it_was_given():
    ask: dict[str, Any] = {
        "name": "x",
        "purpose": "p",
        "kind": "dna",
        "why_needed": "w",
        "why_not_composable": "w",
        "example": "e",
    }
    with pytest.raises(ValidationError, match=r"not 'valid_dna'"):
        ToolRequest(node="score", output="valid_dna", **ask)
    with pytest.raises(ValidationError, match=r"not None"):
        ToolRequest(node="tool", **ask)


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
