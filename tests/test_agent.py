import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.agent import Hypothesis, build_agent
from node_dag.dag import Dag
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.dag.activities import run_filter, run_score, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.run_hypothesis import hypotheses_dir, run_hypothesis, save_hypothesis
from temporal.ui.app import _hypothesis_row

PROTEIN = f"dna_to_protein__{DnaToProteinConfig().config_hash}"
REF = Dna(sequence="ATGGCTCTGAAATAA")
SCORE = DnaAtomScoreConfig(reference=REF)
SCORER = f"dna_atom_score__{SCORE.config_hash}"


def _dag(**steps: dict) -> dict:
    return {"inputs": {"seq": "dna"}, "steps": steps}


def _node(node: str, source: str, port: str = "sequence") -> dict:
    return {"node": node, "inputs": {port: source}}


# protein2 reads an AminoAcidSequence, but port sequence takes Dna.
BAD = _dag(protein=_node(PROTEIN, "seq"), protein2=_node(PROTEIN, "protein"))
GOOD = _dag(protein=_node(PROTEIN, "seq"))
CREATE_PROTEIN = (
    "create_node",
    {"config": {"name": "dna_to_protein"}, "description": "translate each sequence"},
)


def _call(name: str, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, args)])


def _reply(info: AgentInfo, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


def _submit(info: AgentInfo, dag: dict) -> ModelResponse:
    return _reply(info, {"hypothesis": "convert DNA to protein", **dag})


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart | RetryPromptPart]:
    return [
        p for p in messages[-1].parts if isinstance(p, ToolReturnPart | RetryPromptPart)
    ]


def _content(part: ToolReturnPart | RetryPromptPart) -> dict:
    assert isinstance(part.content, dict)
    return part.content


def _turn(messages: list[ModelMessage]) -> int:
    return sum(isinstance(m, ModelResponse) for m in messages)


async def test_agent_makes_nodes_one_by_one_and_fixes_a_rejected_dag(results_dir):
    """A scripted model: list, describe, create a node, submit a bad DAG, then fix it."""
    seen: list[str] = []
    calls = [
        ("list_nodes", {}),
        ("list_registry", {}),
        ("describe_node", {"name": "dna_to_protein"}),
        CREATE_PROTEIN,
    ]

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(str(p.content) for p in _returns(messages))
        turn = _turn(messages)
        if turn < len(calls):
            return _call(*calls[turn])
        return _submit(info, [BAD, GOOD][turn - len(calls)])

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    hyp = Hypothesis(
        goal="convert DNA to protein", inputs={"seq": [Dna(sequence="ATG")]}
    )
    out = (await agent.run(hyp.goal, deps=hyp)).output

    assert out.dag == Dag.model_validate(
        _dag(
            protein={
                "config": {"name": "dna_to_protein"},
                "inputs": {"sequence": "seq"},
            }
        )
    )
    assert out.hypothesis == "convert DNA to protein"
    listing, registry, schema, created, error = seen
    assert '"inputs": {"sequence": "dna"}' in listing
    assert registry == "[]"  # Nothing made yet.
    assert "'x-node'" in schema  # The ports reach the model with the schema.
    assert PROTEIN in created
    assert "port 'sequence' takes Dna, but 'protein' gives AminoAcidSequence" in error


async def test_agent_cannot_use_a_node_it_did_not_register(results_dir):
    errors: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        errors.extend(str(p.content) for p in _returns(messages))
        if _turn(messages) == 0:
            return _submit(info, _dag(protein=_node("dna_to_protein__deadbeef", "seq")))
        return _submit(info, GOOD) if _turn(messages) == 2 else _call(*CREATE_PROTEIN)

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    hyp = Hypothesis(goal="translate", inputs={"seq": [Dna(sequence="ATG")]})
    out = (await agent.run(hyp.goal, deps=hyp)).output
    assert out.dag is not None
    assert "Not registered: ['dna_to_protein__deadbeef']" in errors[0]


async def test_agent_sees_the_score_columns_a_scorer_adds_and_filters_on_one(
    results_dir,
):
    """A filter on a column needs its scorer first. The scorer's reply names the column."""
    seen: list[ToolReturnPart | RetryPromptPart] = []
    scorer = {
        "config": {
            "name": "dna_atom_score",
            "reference": {"kind": "dna", "sequence": REF.sequence},
        },
        "description": "atom count and protein changes against the reference",
    }

    def filter_on(column: str) -> tuple[str, dict]:
        return "create_node", {
            "config": {"name": "at_most", "column": column, "threshold": 500},
            "description": "keep sequences with at most 500 atoms",
        }

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(_returns(messages))
        match _turn(messages):
            case 0:  # The scorer is not made yet.
                return _call(*filter_on(SCORE.columns()["atom_count"]))
            case 1:
                return _call("create_node", scorer)
            case 2:  # Copy the column from the scorer's reply.
                column = _content(seen[-1])["score_columns"]["atom_count"]
                return _call(*filter_on(column))
            case _:
                small = f"at_most__{AtMostConfig(column=SCORE.columns()['atom_count'], threshold=500).config_hash}"
                return _submit(
                    info,
                    _dag(
                        scored=_node(SCORER, "seq"),
                        small=_node(small, "scored", "items"),
                    ),
                )

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    hyp = Hypothesis(goal="small", inputs={"seq": [REF]})
    out = (await agent.run(hyp.goal, deps=hyp)).output

    rejected, scored, filtered = seen[:3]
    assert isinstance(rejected, RetryPromptPart)
    assert "Register the scorer first" in str(rejected.content)
    assert _content(scored)["score_columns"] == SCORE.columns()
    assert _content(scored)["new"] is True
    assert _content(filtered)["filters_on"] == SCORE.columns()["atom_count"]
    assert out.dag is not None
    assert out.dag.steps["scored"].config == SCORE
    small = out.dag.steps["small"].config
    assert isinstance(small, AtMostConfig)
    assert small.column == SCORE.columns()["atom_count"]


async def test_run_hypothesis_builds_runs_and_verifies(results_dir):
    """The verifier gets the outcome of the run, and its verdict lands on the Hypothesis."""

    def builder(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            p.content for p in messages[0].parts if isinstance(p, UserPromptPart)
        )
        # The builder is shown the inputs themselves, to fill in references and thresholds.
        assert '"sequences": ["ATGATGATG", "AAATTTGGG"]' in str(prompt)
        if _turn(messages) == 0:
            return _call(*CREATE_PROTEIN)
        return _submit(info, GOOD)

    def verifier(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            p.content for p in messages[0].parts if isinstance(p, UserPromptPart)
        )
        sent = json.loads(str(prompt))
        assert sent["hypothesis"] == "convert DNA to protein"
        got = [i["sequence"] for i in sent["outcome"]["values"]["protein"]["items"]]
        assert [i["sequence"] for i in sent["inputs"]["seq"]] == [
            "ATGATGATG",
            "AAATTTGGG",
        ]
        return _reply(
            info, {"achieved": len(got) == 2, "reason": f"Got proteins: {got}"}
        )

    hyp = Hypothesis(
        goal="Convert DNA sequence to protein.",
        inputs={"seq": [Dna(sequence="ATGATGATG"), Dna(sequence="AAATTTGGG")]},
    )
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        with ThreadPoolExecutor() as pool:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[DagWorkflow],
                activities=[run_tool, run_score, run_filter, save_workflow],
                activity_executor=pool,
            ):
                done = await run_hypothesis(
                    hyp, env.client, FunctionModel(builder), FunctionModel(verifier)
                )

    assert done.hypothesis == "convert DNA to protein"
    assert [p.stem for p in (results_dir / "registry").iterdir()] == [PROTEIN]
    assert done.dag == Dag.model_validate(
        _dag(
            protein={
                "config": {"name": "dna_to_protein"},
                "inputs": {"sequence": "seq"},
            }
        )
    )
    assert done.outcome is not None
    assert [i.sequence for i in done.outcome.values["protein"].items] == ["MMM", "KFG"]
    assert done.verdict is not None
    assert done.verdict.achieved

    # Saved, with its run, for the hypotheses page.
    (path,) = hypotheses_dir().iterdir()
    row = _hypothesis_row(path)
    assert row.hypothesis == done
    assert row.status == "achieved"
    assert row.progress is not None
    assert row.progress.steps == {"protein": "done"}


def test_a_hypothesis_without_a_dag_is_building():
    hyp = Hypothesis(
        goal="Convert DNA to protein.", inputs={"seq": [Dna(sequence="ATG")]}
    )
    save_hypothesis(hyp)
    (path,) = hypotheses_dir().iterdir()
    assert _hypothesis_row(path).status == "building"


def test_goals_are_distinct_newest_first_with_latest_inputs():
    import os
    import time

    from temporal.ui.app import _goals

    for goal, seq in [("a", "ATG"), ("b", "AAA"), ("a", "TTT")]:
        save_hypothesis(Hypothesis(goal=goal, inputs={"seq": [Dna(sequence=seq)]}))
        time.sleep(0.01)
    paths = sorted(hypotheses_dir().iterdir(), key=lambda p: p.stat().st_mtime)
    for i, p in enumerate(paths):  # mtimes can tie on coarse filesystems.
        os.utime(p, (1000 + i, 1000 + i))
    goals = _goals([_hypothesis_row(p) for p in paths])
    assert [(g.goal, g.hypotheses) for g in goals] == [("a", 2), ("b", 1)]
    assert goals[0].inputs == {"seq": [Dna(sequence="TTT")]}


def test_a_hypothesis_from_an_older_schema_is_skipped_not_fatal():
    """A file written before a schema change must not take out the whole listing."""
    from temporal.ui.app import _saved_rows

    save_hypothesis(Hypothesis(goal="good", inputs={"seq": [Dna(sequence="ATG")]}))
    stale = hypotheses_dir() / "stale.json"
    # inputs.seq was a bare entity before it became a list.
    stale.write_text(
        '{"id": "old", "goal": "old", "inputs": {"seq": {"kind": "dna", "sequence": "ATG"}}}'
    )

    rows = _saved_rows()
    assert [r.hypothesis.goal for r in rows] == ["good"]


def test_search_nodes_finds_and_ranks_by_intent():
    from node_dag.agent import search_nodes

    # Query matching expression scoring
    expr_results = search_nodes(query="score expression translation", input_type="dna")
    assert len(expr_results) >= 1
    assert expr_results[0]["name"] == "ostir_expression"
    assert "score sequences via expression / translation initiation" in expr_results[0]["intents"]

    # A port that takes any nucleic acid matches RNA, and a DNA-only one does not.
    rna_names = [r["name"] for r in search_nodes(input_type="rna")]
    assert "ostir_expression" in rna_names
    assert "dna_to_protein" not in rna_names

    # Query matching atom count
    atom_results = search_nodes(query="reduce atom count", input_type="dna")
    assert len(atom_results) >= 1
    assert atom_results[0]["name"] == "dna_atom_score"

    # Category filtering
    gen_results = search_nodes(category="generation")
    assert {"mutate_synonymous", "recode_codons"} <= {r["name"] for r in gen_results}
    assert "ostir_expression" not in {r["name"] for r in gen_results}

    # Translation
    trans_results = search_nodes(query="translate to protein")
    assert trans_results[0]["name"] == "dna_to_protein"


async def test_agent_rejects_optimization_goal_without_generation(results_dir):
    seen_errors: list[str] = []
    scorer_id = f"dna_atom_score__{SCORE.config_hash}"

    calls = [
        ("list_registry", {}),
        (
            "create_node",
            {
                "config": {"name": "dna_atom_score", "reference": {"sequence": REF.sequence}},
                "description": "score atoms",
            },
        ),
    ]

    def builder(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for p in _returns(messages):
            if isinstance(p, RetryPromptPart):
                seen_errors.append(str(p.content))
        turn = _turn(messages)
        if turn < len(calls):
            return _call(*calls[turn])
        return _reply(
            info,
            {
                "hypothesis": "score only",
                "inputs": {"seq": "dna"},
                "steps": {
                    "scored": {
                        "node": scorer_id,
                        "inputs": {"sequence": "seq"},
                    }
                },
            },
        )

    agent = build_agent(FunctionModel(builder), Registry(results_dir))
    hyp = Hypothesis(
        goal="lower the atom count of the sequence",
        inputs={"seq": [REF]},
    )
    with pytest.raises(Exception):
        await agent.run("build", deps=hyp)

    assert any("no generation node" in err for err in seen_errors)


async def test_agent_rejects_trivial_expression_threshold(results_dir):
    seen_errors: list[str] = []
    calls = [
        (
            "create_node",
            {
                "config": {"name": "ostir_expression", "utr": "AGGAGGTAAAAA"},
                "description": "score expression",
            },
        ),
        (
            "create_node",
            {
                "config": {
                    "name": "at_least",
                    "column": "ostir_expression__mock__expression",
                    "threshold": 0.0,
                },
                "description": "filter expression",
            },
        ),
    ]

    def builder(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for p in _returns(messages):
            if isinstance(p, RetryPromptPart):
                seen_errors.append(str(p.content))
        turn = _turn(messages)
        if turn < len(calls):
            return _call(*calls[turn])
        # Find created filter node id
        filter_id = [n.id for n in Registry(results_dir).all() if "at_least" in n.id][0]
        return _reply(
            info,
            {
                "hypothesis": "filter at 0.0",
                "inputs": {"seq": "dna"},
                "steps": {
                    "filt": {
                        "node": filter_id,
                        "inputs": {"items": "seq"},
                    }
                },
            },
        )

    registry = Registry(results_dir)
    # Pre-register scorer so filter column is accepted by registry
    from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
    ostir = OstirExpressionConfig(utr="AGGAGGTAAAAA")
    registry.register(ostir, "score expression")
    calls[1][1]["config"]["column"] = ostir.columns()["expression"]

    agent = build_agent(FunctionModel(builder), registry)
    hyp = Hypothesis(
        goal="measure expression",
        inputs={"seq": [REF]},
    )
    with pytest.raises(Exception):
        await agent.run("build", deps=hyp)

    assert any("allows every sequence to pass trivially" in err for err in seen_errors)
