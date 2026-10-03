from typing import Any, cast

from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_agent import _reply, _returns, _turn

from node_dag.agent import Hypothesis, build_agent
from node_dag.nodes.base import BaseScoreConfig
from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.plan import Attempt, Criterion, Critique, Plan, ToolRequest
from node_dag.registry import Registry
from node_dag.types import Dna

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
