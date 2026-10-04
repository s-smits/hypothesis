from typing import Any, cast

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_agent import _reply, _returns, _turn

from node_dag.agent import Hypothesis, build_agent, step_config
from node_dag.nodes.base import BaseScoreConfig
from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.plan import Attempt, Criterion, Critique, Plan, ToolRequest
from node_dag.registry import Registry
from node_dag.types import AminoAcidSequence, Dna, Entity

HYP = Hypothesis(
    goal="remove TCG",
    inputs={"seq": [Dna(sequence="ATGTCGTAA")]},
    criteria=[Criterion(id="no_tcg", claim="no TCG remains")],
)
COLUMN = CodonCountConfig(codons=("TCG",)).columns()["count"]
ASK: dict[str, Any] = {
    "purpose": "p",
    "kind": "dna",
    "why_needed": "w",
    "why_not_composable": "codon_count only counts",
    "example": "e",
}


def _step(node: str, source: str, port: str = "sequence", **config) -> dict:
    return {"node": node, "config": config, "inputs": {port: source}, "why": "w"}


def _plan(**kw) -> dict:
    steps = {
        "counted": _step("codon_count", "seq", codons=["TCG"]),
        "small": _step("at_most", "counted", "items", column=COLUMN, threshold=0),
    }
    ask = [{"criterion": "no_tcg", "step": "small", "branch": "yes", "claim": "c"}]
    return {
        "hypothesis": "h",
        "expected": "e",
        "inputs": {"seq": "dna"},
        "steps": steps,
        "assertions": ask,
        **kw,
    }


async def _run(
    results_dir, *plans: dict, hyp: Hypothesis = HYP
) -> tuple[Plan, list[str]]:
    errors: list[str] = []

    def script(messages: list, info: AgentInfo):
        errors.extend(str(p.content) for p in _returns(messages))
        return _reply(info, plans[_turn(messages)])

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    return (await agent.run("go", deps=hyp)).output, errors


async def test_a_plan_may_use_a_node_nobody_has_written(results_dir):
    req = ToolRequest(name="gc_count", node="score", output=["gc"], **ASK)
    column = cast("BaseScoreConfig", req.stand_in()()).columns()["gc"]
    steps = {
        "counted": _step("gc_count", "seq"),
        "small": _step("at_most", "counted", "items", column=column, threshold=0),
    }
    out, errors = await _run(
        results_dir, _plan(steps=steps, requests={"gc_count": req.model_dump()})
    )
    assert errors == [] and out.requests["gc_count"] == req


async def test_an_assertion_on_a_criterion_or_step_the_plan_lacks_is_sent_back(
    results_dir,
):
    stray = {**_says("small", "yes"), "criterion": "made_up"}
    nowhere = _says("nowhere", "produced")
    for bad, why in [
        (_plan(assertions=[_says("small", "yes"), stray]), "unknown criterion"),
        (_plan(assertions=[_says("small", "yes"), nowhere]), "not a step of the plan"),
    ]:
        _, errors = await _run(results_dir, bad, _plan())
        assert why in errors[0], (why, errors)


async def test_a_filter_that_keeps_some_can_be_asserted_as_produced(results_dir):
    keeps_some = _plan(
        assertions=[
            {"criterion": "no_tcg", "step": "small", "branch": "produced", "claim": "c"}
        ]
    )
    _, errors = await _run(results_dir, keeps_some)
    assert errors == []


async def test_the_guards_send_a_bad_plan_back(results_dir):
    shadow = ToolRequest(name="at_most", node="filter", **ASK).model_dump()
    bare = _plan(assertions=[])
    on_tool = _plan(
        assertions=[
            {"criterion": "no_tcg", "step": "counted", "branch": "yes", "claim": "c"}
        ]
    )
    both = _plan(
        assertions=[
            {"criterion": "no_tcg", "step": "small", "branch": "yes", "claim": "c"},
            {"criterion": "no_tcg", "step": "small", "branch": "no", "claim": "c"},
        ]
    )
    for bad, why in [
        (_plan(requests={"at_most": shadow}), "already exists"),
        (bare, "No assertion covers ['no_tcg']"),
        (on_tool, "is not a filter: use branch produced"),
        (both, "cannot both hold"),
    ]:
        _, errors = await _run(results_dir, bad, _plan())
        assert why in errors[0], (why, errors)


async def test_a_goal_with_nothing_to_filter_may_assert_that_a_step_produced(
    results_dir,
):
    ask = {"criterion": "no_tcg", "step": "counted", "branch": "produced", "claim": "c"}
    out, errors = await _run(results_dir, _plan(assertions=[ask]))
    assert errors == [] and out.assertions[0].branch == "produced"


def _says(step: str, branch: str) -> dict:
    return {"criterion": "no_tcg", "step": step, "branch": branch, "claim": "c"}


async def test_no_and_produced_on_one_filter_are_sent_back(results_dir):
    """no needs the yes branch empty and produced needs it not empty: never both."""
    clash = _plan(assertions=[_says("small", "no"), _says("small", "produced")])
    _, errors = await _run(results_dir, clash, _plan())
    assert "cannot both hold" in errors[0] and "['small']" in errors[0]
    assert "no" in errors[0] and "produced" in errors[0]


async def test_assertions_that_can_hold_together_are_not_sent_back(results_dir):
    """yes with produced on one filter, or yes on one filter and no on another, can hold."""
    both_kinds = _plan(assertions=[_says("small", "yes"), _says("small", "produced")])
    audit = {
        **_plan()["steps"],
        "rest": _step("at_most", "small.no", "items", column=COLUMN, threshold=0),
    }
    split = _plan(steps=audit, assertions=[_says("small", "yes"), _says("rest", "no")])
    for ok in (both_kinds, split):
        out, errors = await _run(results_dir, ok)
        assert errors == [] and len(out.assertions) == 2


async def test_a_step_with_an_unknown_node_is_named_with_the_ids_it_could_use(
    results_dir,
):
    scorer, _ = Registry(results_dir / "registry").register(
        CodonCountConfig(codons=("TCG",)), "counts TCG"
    )
    bad = _plan(
        steps={
            **_plan()["steps"],
            "small": _step("at_most__deadbeef", "counted", "items"),
        }
    )
    _, errors = await _run(results_dir, bad, _plan())
    assert "Step 'small'" in errors[0] and "'at_most__deadbeef'" in errors[0]
    assert scorer.id in errors[0] and "requests" in errors[0]
    assert "tagged-union" not in errors[0]


def test_the_ids_an_unknown_node_message_lists_are_capped_and_counted(results_dir):
    registry = Registry(results_dir / "registry")
    codons = [a + b + c for a in "ACGT" for b in "ACGT" for c in "ACGT"][:30]
    for codon in codons:
        registry.register(CodonCountConfig(codons=(codon,)), "counts a codon")
    bad = Plan.model_validate(
        _plan(
            steps={
                **_plan()["steps"],
                "small": _step("at_most__deadbeef", "counted", "items"),
            }
        )
    )
    with pytest.raises(ValueError) as raised:
        step_config(bad, "small", registry)
    message = str(raised.value)
    assert message.count("codon_count__") == 20 and "and 10 more" in message
    assert "list_registry" in message and "Built-in names" in message


async def test_a_config_that_does_not_fit_its_node_names_the_step_and_the_field(
    results_dir,
):
    no_threshold = _step("at_most", "counted", "items", column=COLUMN)
    steps = {**_plan()["steps"], "small": no_threshold}
    _, errors = await _run(results_dir, _plan(steps=steps), _plan())
    assert "Step 'small'" in errors[0] and "threshold" in errors[0]
    assert "tagged-union" not in errors[0] and "pydantic.dev" not in errors[0]


async def test_a_requested_filter_with_no_column_is_told_where_to_put_it(results_dir):
    ask = ToolRequest(name="valid_dna", node="filter", **ASK).model_dump()
    steps = {**_plan()["steps"], "small": _step("valid_dna", "counted", "items")}
    _, errors = await _run(
        results_dir, _plan(steps=steps, requests={"valid_dna": ask}), _plan()
    )
    assert "Step 'small'" in errors[0] and "column" in errors[0]
    assert "config" in errors[0] and "pydantic.dev" not in errors[0]


async def test_a_wiring_that_already_ran_is_not_resubmitted_and_a_critique_must_be_addressed(
    results_dir,
):
    tried = Plan.model_validate(_plan())
    crit = Critique(diagnosis="d", root_cause="wrong_config", evidence=["e"], fix="f")
    hyp = HYP.model_copy(
        update={"attempts": [Attempt(round=1, plan=tried, critique=crit)]}
    )
    other = _plan(
        steps={
            **_plan()["steps"],
            "small": _step("at_most", "counted", "items", column=COLUMN, threshold=1),
        }
    )
    out, errors = await _run(
        results_dir,
        _plan(),
        other,
        {**other, "addresses_critique": "allows one"},
        hyp=hyp,
    )
    assert (
        "already ran" in errors[0]
        and "addresses_critique" in errors[1]
        and out.addresses_critique
    )


async def test_the_same_wiring_with_a_stronger_assertion_is_a_repair_not_a_repeat(
    results_dir,
):
    """Arm C a9d91571 r2: produced -> yes on two all-pass filters was sent back as a repeat."""
    weak = Plan.model_validate(_plan(assertions=[_says("small", "produced")]))
    crit = Critique(diagnosis="d", root_cause="wrong_wiring", evidence=["e"], fix="f")
    hyp = HYP.model_copy(
        update={"attempts": [Attempt(round=1, plan=weak, critique=crit)]}
    )
    stronger = _plan(
        assertions=[_says("small", "yes")], addresses_critique="asserts yes now"
    )
    out, errors = await _run(results_dir, stronger, hyp=hyp)
    assert errors == [] and out.assertions[0].branch == "yes"


async def test_a_repeat_that_weakens_moves_or_only_repeats_an_assertion_is_sent_back(
    results_dir,
):
    strong = Plan.model_validate(_plan(assertions=[_says("small", "yes")]))
    crit = Critique(diagnosis="d", root_cause="wrong_wiring", evidence=["e"], fix="f")
    hyp = HYP.model_copy(
        update={"attempts": [Attempt(round=1, plan=strong, critique=crit)]}
    )
    fixed = {"addresses_critique": "x"}
    changed = _plan(
        steps={
            **_plan()["steps"],
            "small": _step("at_most", "counted", "items", column=COLUMN, threshold=1),
        },
        **fixed,
    )
    for bad in [
        [_says("small", "produced")],  # weaker
        [_says("counted", "produced")],  # moved to another step
        [_says("small", "yes")],  # the same
    ]:
        _, errors = await _run(
            results_dir, _plan(assertions=bad, **fixed), changed, hyp=hyp
        )
        assert "already ran" in errors[0] and "strengthen" in errors[0]


async def test_the_plan_tool_is_not_strict_so_its_dicts_can_have_keys(results_dir):
    """Strict output forces additionalProperties false, so steps could only be {}."""
    seen = []

    def script(messages: list, info: AgentInfo):
        seen.append(info.output_tools[0])
        return _reply(info, _plan())

    await build_agent(FunctionModel(script), Registry(results_dir / "registry")).run(
        "go", deps=HYP
    )
    assert seen[0].strict is False
    assert (
        seen[0]
        .parameters_json_schema["properties"]["steps"]
        .get("additionalProperties")
        is not False
    )


def _beats_plan(reference: Entity) -> dict:
    better = _step(
        "beats_reference",
        "counted",
        "items",
        column=COLUMN,
        reference=reference.model_dump(mode="json"),
        scored_in="counted",
    )
    ask = {"criterion": "no_tcg", "step": "better", "branch": "produced", "claim": "c"}
    steps = {**_plan()["steps"], "better": better}
    return _plan(steps=steps, assertions=[ask])


async def test_a_beats_reference_plan_whose_reference_is_an_input_is_accepted(
    results_dir,
):
    _, errors = await _run(results_dir, _beats_plan(HYP.inputs["seq"][0]))
    assert errors == []


async def test_a_beats_reference_plan_whose_reference_is_not_an_input_is_sent_back(
    results_dir,
):
    good = _beats_plan(HYP.inputs["seq"][0])
    made_up = Dna(sequence="ATGTCCTAA")  # The right kind, but not an input.
    wrong_kind = AminoAcidSequence(sequence="MS")
    for bad in (made_up, wrong_kind):
        _, errors = await _run(results_dir, _beats_plan(bad), good)
        assert len(errors) == 1, errors
        assert "Step 'better'" in errors[0] and "one of the input entities" in errors[0]
        assert "dna ATGTCGTAA" in errors[0] and "'counted'" in errors[0]
        assert "scored_in" in errors[0]


TWO = HYP.model_copy(
    update={
        "inputs": {
            "seq": [Dna(sequence="ATGTCGTAA")],
            "other": [Dna(sequence="ATGTCCTAA")],
        }
    }
)


def _scored_in(scored_in: str, *, reference: Entity, **steps: dict) -> dict:
    """A plan on TWO that keeps what beats ``reference`` in the table of ``scored_in``."""
    better = _step(
        "beats_reference",
        "counted",
        "items",
        column=COLUMN,
        reference=reference.model_dump(mode="json"),
        scored_in=scored_in,
    )
    ask = {"criterion": "no_tcg", "step": "better", "branch": "produced", "claim": "c"}
    steps = {**_plan()["steps"], **steps, "better": better}
    return _plan(inputs={"seq": "dna", "other": "dna"}, steps=steps, assertions=[ask])


async def test_a_reference_that_the_scored_in_step_never_reads_is_sent_back(
    results_dir,
):
    other = TWO.inputs["other"][0]
    ref_score = {"refscore": _step("codon_count", "other", codons=["TCG"])}
    good = _scored_in("refscore", reference=other, **ref_score)
    # The reference is an input, but the step named holds only the entities of seq.
    _, errors = await _run(
        results_dir, _scored_in("counted", reference=other), good, hyp=TWO
    )
    assert len(errors) == 1, errors
    for part in ("Step 'better'", "ATGTCCTAA", "'other'", "'counted'", "'seq'"):
        assert part in errors[0], (part, errors)


async def test_a_reference_that_the_scored_in_step_may_hold_is_accepted(results_dir):
    first = TWO.inputs["seq"][0]
    kept = {"small": _plan()["steps"]["small"]}
    mutate = {"mutated": _step("mutate_synonymous", "seq", seed=1)}
    counted = _step("codon_count", "mutated", codons=["TCG"])
    for plan in (
        # Scored directly from the input that has it, or from a branch of a filter on it.
        _scored_in("counted", reference=first),
        _scored_in("small.yes", reference=first, **kept),
        # A tool can make any entity, the reference included, so that is left to the run.
        _scored_in("counted", reference=first, **mutate, counted=counted),
    ):
        _, errors = await _run(results_dir, plan, hyp=TWO)
        assert errors == [], errors


async def test_a_declared_result_must_be_a_step_output(results_dir):
    for result in ("seq", "small", "nowhere"):
        _, errors = await _run(results_dir, _plan(result_source=result), _plan())
        assert "is not the output of a step" in errors[0] and "'small.yes'" in errors[0]
    for result in ("counted", "small.yes", "small.no"):
        out, errors = await _run(results_dir, _plan(result_source=result))
        assert errors == [] and out.result_source == result, result


async def test_a_proof_past_a_tool_does_not_speak_for_the_result_before_it(results_dir):
    """A tool makes new entities: a filter after one tests those, not the result."""
    steps = {
        **_plan()["steps"],
        "fix": _step("recode_targeted", "small.yes", targeted_codons=["TCG"]),
        "after": _step("codon_count", "fix", codons=["TCG"]),
        "clean": _step("at_most", "after", "items", column=COLUMN, threshold=0),
    }
    on_clean = [_says("clean", "yes")]
    wiring = {"steps": steps, "assertions": on_clean}
    _, errors = await _run(
        results_dir, _plan(result_source="counted", **wiring), _plan()
    )
    assert "without a tool between" in errors[0] and "['no_tcg']" in errors[0]
    out, errors = await _run(results_dir, _plan(result_source="fix", **wiring))
    assert errors == [] and out.result_source == "fix"
