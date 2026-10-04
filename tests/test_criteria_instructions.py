"""The criteria stage asks for what the goal states, and nothing it would then have to be refused for.

The criteria are the contract the builder is marked against: it asserts each on a step of a
DAG of existing nodes, code decides whether the assertion held, and the verifier decides
whether the assertions cover the goal. A criterion no node's output can settle cannot be met,
so a goal "convert this RNA to protein" that came back with "exactly 20 residues, no stop
symbol" could not be met by dna_to_protein, which writes the stop as `*`, and the run spent
six rounds and 554,000 tokens on it. Of 11 recorded criteria sets, 10 held such a clause.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from node_dag.agent import (
    CRITERIA_INSTRUCTIONS,
    NODES,
    VERIFY_INSTRUCTIONS,
    Hypothesis,
    criteria_agent,
)
from node_dag.nodes.base import BaseScoreConfig
from node_dag.plan import Criterion
from node_dag.types import Dna
from temporal.hypothesis.activities import Stage, derive_criteria


def test_the_criteria_instructions_ask_for_what_the_goal_states_and_nothing_else():
    text = " ".join(CRITERIA_INSTRUCTIONS.split())
    assert "one for each requirement the goal states" in text
    assert "none for anything it does not" in text
    for invented in ("length", "reading frame", "start or stop codon", "alphabet"):
        assert invented in text
    assert 'Say each requirement once: "keep the ones above X" is one claim' in text
    assert "never with the input an output came from" in text
    assert "State what must be true, not how to do it." in text


def test_no_node_measures_what_the_criteria_are_told_not_to_add():
    """The premise of the rule: no score reads a start or stop codon, a frame or a source.

    If a node ever does, the rule is out of date and this fails, so that someone reads it.
    """
    names = {
        score
        for node in NODES.values()
        if issubclass(node, BaseScoreConfig)
        for score in node.output
    }
    assert names
    for word in ("start", "stop", "frame", "parent", "source", "lineage", "valid"):
        assert not [n for n in names if word in n], word
    with pytest.raises(ValidationError):  # The alphabet is a property of the kind.
        Dna(sequence="ACGN")


async def test_the_criteria_agent_is_given_the_text_and_the_schema_says_the_same(
    monkeypatch,
):
    seen: dict[str, Any] = {}

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["instructions"] = info.instructions
        seen["schema"] = " ".join(
            json.dumps(info.output_tools[0].parameters_json_schema)
            .replace("\\n", " ")
            .split()
        )
        reply = {"response": [{"id": "protein_kept", "claim": "the protein is kept"}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])

    monkeypatch.setattr(
        "temporal.hypothesis.activities.criteria_agent",
        lambda model: criteria_agent(FunctionModel(script)),
    )
    hyp = Hypothesis(
        goal="Raise the expression of the sequences without changing their proteins.",
        inputs={"seqs": [Dna(sequence="ATGGCTCTGAAATAA")]},
    )
    out = await derive_criteria(Stage(hyp=hyp, model="test"))

    assert seen["instructions"] == CRITERIA_INSTRUCTIONS
    assert [(c.id, c.source) for c in out.criteria] == [("protein_kept", "derived")]
    # The model also reads the Criterion model: it must not say what the text does not.
    assert "rather than a number" in seen["schema"] and "to judge" not in seen["schema"]
    assert "to judge" not in " ".join((Criterion.__doc__ or "").split())


def test_the_verifier_lets_a_conversion_step_cover_a_criterion_that_it_converts():
    """Else a goal that only converts can never be accepted, whatever the DAG does.

    "Convert this RNA to protein" came back with the right protein in 3 of 3 rounds and
    `agrees` true each time, and `covers_goal` false each time: the one assertion a
    converter can carry is "produced", which says the step gave output, not what it holds.
    The rewiring that followed (a no-op filter, a round trip through the same node)
    added no check either. A type is already trusted for its alphabet, so is a converter.
    """
    text = " ".join(VERIFY_INSTRUCTIONS.split())
    assert "converts one kind into another" in text
    assert (
        '"produced" on that step covers a criterion that its output is that conversion'
        in text
    )
    assert "and nothing beyond it" in text
    assert "length, start codon and stop codon are not guaranteed by type" in text
