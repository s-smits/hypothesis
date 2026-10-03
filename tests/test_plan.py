"""The builder may name a tool nobody has written, and must pay for the privilege.

Every guard in ``submit_plan`` comes back to the model as a retry, so these tests script a
model that submits something wrong, reads the complaint, and fixes it. No network.
"""

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from node_dag.agent import PlanDeps, plan_agent
from node_dag.plan import Criterion

NO_TCG = Criterion(id="no_tcg", claim="no TCG codon remains in the sequence")

DEPS = PlanDeps(
    goal="remove every TCG codon without changing the protein",
    criteria=[NO_TCG],
    input_kinds={"seq": "dna"},
)

WHY = "recode_codons cannot do this and mutate_synonymous picks codons at random"

# A plan over nodes that all exist: recode, then prove the target codons are gone.
REAL = {
    "hypothesis": "recode the sequence, then check no TCG survived",
    "expected": "the clean step takes its yes branch, so no TCG remains",
    "inputs": {"seq": "dna"},
    "steps": {
        "recoded": {
            "node": "recode_codons",
            "config": {"targets": ["TCG"]},
            "inputs": {"sequence": "seq"},
            "why": "swap each TCG for a synonymous codon",
        },
        "clean": {
            "node": "codons_absent",
            "config": {"codons": ["TCG"]},
            "inputs": {"sequence": "recoded"},
            "why": "prove no TCG survived the recoding",
        },
    },
    "assertions": [
        {
            "criterion": "no_tcg",
            "step": "clean",
            "branch": "yes",
            "claim": "no TCG codon remains",
        }
    ],
    "reasons": {
        "recode_codons": "it targets named codons, unlike the random mutator",
        "codons_absent": "it settles the criterion with a branch, not an opinion",
    },
}

# The same plan, but the checking step is a node nobody has written yet.
WANTS_TOOL = {
    **REAL,
    "steps": {
        "recoded": REAL["steps"]["recoded"],
        "clean": {
            "node": "homopolymer_free",
            "config": {"low": 0.4, "high": 0.6},
            "inputs": {"sequence": "recoded"},
            "why": "check the GC content stayed in a viable range",
        },
    },
    "requests": [
        {
            "name": "homopolymer_free",
            "node": "decision",
            "purpose": "Yes when no base repeats more than a given number of times.",
            "category": "filter",
            "inputs": {"sequence": "dna"},
            "forwards": "sequence",
            "why_needed": "long single-base runs break synthesis and nothing here counts them",
            "why_not_composable": (
                "codons_absent only tests for whole codons, dna_atom_score counts atoms "
                "rather than bases, and at_least needs a score no node produces for GC"
            ),
            "example": "ATGGCC with limit=3 -> yes; ATGAAAAGCC -> no, four As in a row",
        }
    ],
}


def _submit(info: AgentInfo, plan: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])


def _catalogue(info: AgentInfo, turn: int, nodes: list[str]) -> ModelResponse | None:
    """Make the catalogue calls the guards require, one per turn."""
    if turn == 0:
        return ModelResponse(parts=[ToolCallPart("list_nodes", {})])
    if turn <= len(nodes):
        return ModelResponse(
            parts=[ToolCallPart("describe_node", {"name": nodes[turn - 1]})]
        )
    return None


def _run(plans: list[dict], nodes: list[str], deps: PlanDeps = DEPS):
    """Script a model that reads the catalogue then submits each plan in turn.

    Returns the agent run plus the retry messages the guards sent back.
    """
    retries: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        retries.extend(
            str(p.content)
            for p in messages[-1].parts
            if isinstance(p, RetryPromptPart)
        )
        turn = sum(isinstance(m, ModelResponse) for m in messages)
        if (reply := _catalogue(info, turn, nodes)) is not None:
            return reply
        return _submit(info, plans[min(turn - len(nodes) - 1, len(plans) - 1)])

    return plan_agent(FunctionModel(script)), retries, deps


async def _retries(plans: list[dict], nodes: list[str], deps: PlanDeps = DEPS):
    """Run a scripted plan submission and return the guard complaints it drew.

    A plan the guards never accept exhausts the output retries, which pydantic-ai turns
    into UnexpectedModelBehavior. That is the expected shape of these tests, so swallow
    it and assert on what the guards actually said.
    """
    agent, collected, _ = _run(plans, nodes, deps)
    try:
        await agent.run("plan it", deps=deps)
    except UnexpectedModelBehavior:
        pass
    return collected


async def test_a_plan_may_name_a_tool_that_does_not_exist():
    """The headline behaviour: no cage, and no complaint, for an unwritten node."""
    agent, retries, deps = _run([WANTS_TOOL], ["recode_codons", "dna_atom_score"])
    out = (await agent.run("plan it", deps=deps)).output
    assert retries == [], retries
    assert "homopolymer_free" in out.requests
    assert out.requests["homopolymer_free"].node == "decision"
    assert out.steps["clean"].node == "homopolymer_free"


async def test_a_plan_over_existing_nodes_still_builds_a_real_dag():
    agent, retries, deps = _run([REAL], ["recode_codons", "codons_absent"])
    out = (await agent.run("plan it", deps=deps)).output
    assert retries == []
    assert out.requests == {}
    assert out.fingerprint()


async def test_a_hypothetical_tool_is_still_typechecked():
    """A requested node is not a wildcard: its declared kinds must line up."""
    broken = {
        **WANTS_TOOL,
        "requests": [{**WANTS_TOOL["requests"][0], "inputs": {"sequence": "score"}}],
    }
    retries = await _retries([broken], ["recode_codons", "dna_atom_score"])
    assert any("takes score" in r or "gives dna" in r for r in retries), retries


async def test_an_invented_kind_is_rejected():
    bad = {
        **WANTS_TOOL,
        "requests": [{**WANTS_TOOL["requests"][0], "inputs": {"sequence": "peptide"}}],
    }
    retries = await _retries([bad], ["recode_codons", "dna_atom_score"])
    # ToolRequest's own validator catches this before G3 runs: the type rejects an
    # invented kind, and G3 is the backstop for a request that reached the guard.
    assert any("peptide" in r and "kind" in r.lower() for r in retries), retries


async def test_a_request_that_shadows_an_existing_node_is_rejected():
    dupe = {
        **WANTS_TOOL,
        "steps": {
            "recoded": REAL["steps"]["recoded"],
            "clean": {**WANTS_TOOL["steps"]["clean"], "node": "codons_absent"},
        },
        "requests": [{**WANTS_TOOL["requests"][0], "name": "codons_absent"}],
    }
    retries = await _retries([dupe], ["recode_codons", "codons_absent"])
    assert any("already exists" in r for r in retries), retries


async def test_a_near_duplicate_must_be_argued_for_not_just_asserted():
    """The agent may win the argument. It just has to make it, by name."""
    vague = {
        **WANTS_TOOL,
        "requests": [
            {
                **WANTS_TOOL["requests"][0],
                "name": "codons_gone",
                "why_not_composable": "nothing in the catalogue does this at all",
            }
        ],
        "steps": {
            "recoded": REAL["steps"]["recoded"],
            "clean": {**WANTS_TOOL["steps"]["clean"], "node": "codons_gone"},
        },
    }
    retries = await _retries([vague], ["recode_codons", "codons_absent"])
    assert any("codons_absent" in r for r in retries), retries


async def test_asking_for_a_tool_without_reading_the_catalogue_is_rejected():
    """You cannot know a tool is missing without having looked."""
    retries: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        retries.extend(
            str(p.content) for p in messages[-1].parts if isinstance(p, RetryPromptPart)
        )
        return _submit(info, WANTS_TOOL)

    agent = plan_agent(FunctionModel(script))
    with pytest.raises(UnexpectedModelBehavior):
        await agent.run("plan it", deps=DEPS)
    assert any("list_nodes" in r for r in retries), retries


async def test_the_request_cap_is_enforced():
    greedy = {
        **WANTS_TOOL,
        "requests": [
            WANTS_TOOL["requests"][0],
            {**WANTS_TOOL["requests"][0], "name": "gc_window"},
            {**WANTS_TOOL["requests"][0], "name": "gc_band"},
        ],
    }
    retries = await _retries([greedy], ["recode_codons", "dna_atom_score"])
    assert any("At most" in r for r in retries), retries


async def test_an_uncovered_criterion_is_rejected():
    """Gap H: the builder cannot quietly drop a criterion it finds inconvenient."""
    deps = PlanDeps(
        goal=DEPS.goal,
        criteria=[NO_TCG, Criterion(id="same_protein", claim="the protein is unchanged")],
        input_kinds={"seq": "dna"},
    )
    retries = await _retries([REAL], ["recode_codons", "codons_absent"], deps)
    assert any("same_protein" in r for r in retries), retries


async def test_an_assertion_on_a_tool_step_is_rejected():
    """Only a decision has a branch, so only a decision can settle a claim."""
    bad = {
        **REAL,
        "assertions": [{**REAL["assertions"][0], "step": "recoded"}],
    }
    retries = await _retries([bad], ["recode_codons", "codons_absent"])
    assert any("a tool" in r for r in retries), retries


async def test_a_repeated_wiring_is_rejected():
    agent, _, deps = _run([REAL], ["recode_codons", "codons_absent"])
    out = (await agent.run("plan it", deps=deps)).output
    # Feed its own fingerprint back as already tried; the next round must change.
    again = PlanDeps(
        goal=deps.goal,
        criteria=deps.criteria,
        input_kinds=deps.input_kinds,
        tried=[out.fingerprint()],
    )
    retries2 = await _retries([REAL], ["recode_codons", "codons_absent"], again)
    assert any("already run this exact wiring" in r for r in retries2), retries2


def test_fingerprint_ignores_prose():
    """Rewording must not count as a new plan."""
    from node_dag.plan import Plan

    a = Plan.model_validate(REAL)
    b = Plan.model_validate(
        {**REAL, "hypothesis": "an entirely different sentence here", "expected": "x" * 20}
    )
    assert a.fingerprint() == b.fingerprint()
    c = Plan.model_validate(
        {
            **REAL,
            "steps": {
                **REAL["steps"],
                "recoded": {**REAL["steps"]["recoded"], "config": {"targets": ["TCA"]}},
            },
        }
    )
    assert a.fingerprint() != c.fingerprint()
