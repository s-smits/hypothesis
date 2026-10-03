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

from node_dag import entrez
from node_dag.agent import Criterion, Hypothesis, build_agent
from node_dag.dag import Dag
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.dag.activities import run_filter, run_score, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.run_hypothesis import hypotheses_dir, run_hypothesis, save_hypothesis
from temporal.ui.app import _hypothesis_row

PROTEIN = f"dna_to_protein__{DnaToProteinConfig().config_hash}"
REF = Dna(sequence="ATGGCTCTGAAATAA")
SCORE = OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA")
SCORER = f"ostir_expression__{SCORE.config_hash}"


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
            "name": "ostir_expression",
            "utr": "TTCTAGAAAGGAGGTAAAAAA",
        },
        "description": "score expression of coding sequence",
    }

    def filter_on(column: str) -> tuple[str, dict]:
        return "create_node", {
            "config": {"name": "at_most", "column": column, "threshold": 500000},
            "description": "keep sequences with at most 500k expression",
        }

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(_returns(messages))
        match _turn(messages):
            case 0:  # The scorer is not made yet.
                return _call(*filter_on(SCORE.columns()["expression"]))
            case 1:
                return _call("create_node", scorer)
            case 2:  # Copy the column from the scorer's reply.
                column = _content(seen[-1])["score_columns"]["expression"]
                return _call(*filter_on(column))
            case _:
                small = f"at_most__{AtMostConfig(column=SCORE.columns()['expression'], threshold=500000).config_hash}"
                return _submit(
                    info,
                    _dag(
                        scored=_node(SCORER, "seq"),
                        small=_node(small, "scored", "items"),
                    ),
                )

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    hyp = Hypothesis(goal="expression", inputs={"seq": [REF]})
    out = (await agent.run(hyp.goal, deps=hyp)).output

    rejected, scored, filtered = seen[:3]
    assert isinstance(rejected, RetryPromptPart)
    assert "Register the scorer first" in str(rejected.content)
    assert _content(scored)["score_columns"] == SCORE.columns()
    assert _content(scored)["new"] is True
    assert _content(filtered)["filters_on"] == SCORE.columns()["expression"]
    assert out.dag is not None
    assert out.dag.steps["scored"].config == SCORE
    small = out.dag.steps["small"].config
    assert isinstance(small, AtMostConfig)
    assert small.column == SCORE.columns()["expression"]


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


async def test_criteria_agent_drafts_a_list_of_criteria():
    from node_dag.agent import criteria_agent

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            p.content for p in messages[0].parts if isinstance(p, UserPromptPart)
        )
        assert "Goal: increase expression" in str(prompt)
        assert "Proposed hypothesis: mutate codons" in str(prompt)
        return _reply(
            info,
            {
                "response": [
                    {"kind": "quantitative", "text": "expression above the input's"},
                    {"kind": "qualitative", "text": "the protein is unchanged"},
                ]
            },
        )

    agent = criteria_agent(FunctionModel(script))
    out = (
        await agent.run("Goal: increase expression\nProposed hypothesis: mutate codons")
    ).output

    assert out == [
        Criterion(kind="quantitative", text="expression above the input's"),
        Criterion(kind="qualitative", text="the protein is unchanged"),
    ]


async def test_criteria_reach_the_builder_prompt_and_the_verifier(results_dir):
    """Criteria the user accepted qualify the goal for both agents."""

    def builder(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            p.content for p in messages[0].parts if isinstance(p, UserPromptPart)
        )
        assert "[quantitative] expression above the input's" in str(prompt)
        assert "[qualitative] the protein is unchanged" in str(prompt)
        if _turn(messages) == 0:
            return _call(*CREATE_PROTEIN)
        return _submit(info, GOOD)

    def verifier(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            p.content for p in messages[0].parts if isinstance(p, UserPromptPart)
        )
        sent = json.loads(str(prompt))
        assert sent["criteria"] == [
            {"kind": "quantitative", "text": "expression above the input's"},
            {"kind": "qualitative", "text": "the protein is unchanged"},
        ]
        return _reply(
            info,
            {
                "achieved": False,
                "reason": "protein held but expression did not improve",
                "criteria": [
                    {"criterion": 0, "met": False, "reason": "same score"},
                    {"criterion": 1, "met": True, "reason": "identical protein"},
                ],
            },
        )

    hyp = Hypothesis(
        goal="Convert DNA to protein.",
        criteria=[
            Criterion(kind="quantitative", text="expression above the input's"),
            Criterion(kind="qualitative", text="the protein is unchanged"),
        ],
        inputs={"seq": [Dna(sequence="ATG")]},
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

    assert done.criteria == hyp.criteria
    # The critic's per-criterion calls land on the verdict, in order.
    assert done.verdict is not None
    assert done.verdict.criteria[0].met is False
    assert done.verdict.criteria[1].met is True
    # And they survive the save the hypotheses page reads.
    (path,) = hypotheses_dir().iterdir()
    row = _hypothesis_row(path)
    assert row.hypothesis.verdict is not None
    assert [j.met for j in row.hypothesis.verdict.criteria] == [False, True]


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

    newer = [Criterion(kind="quantitative", text="expression above baseline")]
    rows = [
        Hypothesis(goal="a", inputs={"seq": [Dna(sequence="ATG")]}),
        Hypothesis(goal="b", inputs={"seq": [Dna(sequence="AAA")]}),
        Hypothesis(goal="a", criteria=newer, inputs={"seq": [Dna(sequence="TTT")]}),
    ]
    for h in rows:
        save_hypothesis(h)
        time.sleep(0.01)
    paths = sorted(hypotheses_dir().iterdir(), key=lambda p: p.stat().st_mtime)
    for i, p in enumerate(paths):  # mtimes can tie on coarse filesystems.
        os.utime(p, (1000 + i, 1000 + i))
    goals = _goals([_hypothesis_row(p) for p in paths])
    assert [(g.goal, g.hypotheses) for g in goals] == [("a", 2), ("b", 1)]
    assert goals[0].inputs == {"seq": [Dna(sequence="TTT")]}
    # The page pre-fills a goal's criteria from its most recent hypothesis.
    assert goals[0].criteria == newer
    assert goals[1].criteria == []


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
    assert (
        "score sequences via expression / translation initiation"
        in expr_results[0]["intents"]
    )

    # A port that takes any nucleic acid matches RNA, and a DNA-only one does not.
    rna_names = [r["name"] for r in search_nodes(input_type="rna")]
    assert "ostir_expression" in rna_names
    assert "dna_to_protein" not in rna_names

    # Query matching expression
    expr_results = search_nodes(
        query="measure translation initiation", input_type="dna"
    )
    assert len(expr_results) >= 1
    assert expr_results[0]["name"] == "ostir_expression"

    # Category filtering
    gen_results = search_nodes(category="generation")
    assert [r["name"] for r in gen_results] == [
        "codon_optimise",
        "domesticate",
        "gc_target_recode",
        "mutate_synonymous",
        "protlib_design",
        "recode_targeted",
        "resample_synonymous",
    ]

    # Translation
    trans_results = search_nodes(query="translate to protein")
    assert trans_results[0]["name"] == "dna_to_protein"


async def test_agent_rejects_optimization_goal_without_generation(results_dir):
    seen_errors: list[str] = []
    scorer_id = f"ostir_expression__{SCORE.config_hash}"

    calls = [
        ("list_registry", {}),
        (
            "create_node",
            {
                "config": {"name": "ostir_expression", "utr": "TTCTAGAAAGGAGGTAAAAAA"},
                "description": "score expression",
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
        goal="increase the expression of the sequence",
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


LACZ = """\
>lcl|J01636.1_cds_AAB59138.1_1 [gene=lacZ] [protein=beta-D-galactosidase] [location=1..15]
ATGGCTCTGAAATAA
"""


async def test_agent_fetches_its_own_inputs_when_the_goal_gives_none(
    results_dir, monkeypatch
):
    """With no inputs given, the builder finds a record and declares the input itself."""
    monkeypatch.setattr(entrez, "_get", lambda *a, **k: LACZ)
    seen: list[str] = []
    calls = [
        ("search_sequences", {"term": 'lacZ[gene] AND "Escherichia coli"[orgn]'}),
        ("fetch_sequences", {"accession": "J01636.1", "gene": "lacZ"}),
        (
            "add_input",
            {
                "name": "seq",
                "source": "NCBI J01636.1 CDS lacZ",
                "handles": ["J01636.1:lacZ"],
            },
        ),
        CREATE_PROTEIN,
    ]

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(str(p.content) for p in _returns(messages))
        turn = _turn(messages)
        # search_sequences is the one call that would reach the network here.
        if turn == 0:
            monkeypatch.setattr(
                entrez, "search", lambda *a, **k: [{"accession": "J01636.1"}]
            )
        return _call(*calls[turn]) if turn < len(calls) else _submit(info, GOOD)

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    hyp = Hypothesis(goal="translate the E. coli lacZ CDS to protein")
    out = (await agent.run(hyp.goal, deps=hyp)).output

    assert out.inputs == {"seq": [Dna(sequence="ATGGCTCTGAAATAA")]}
    assert out.input_sources == {"seq": "NCBI J01636.1 CDS lacZ"}
    assert out.dag is not None
    _, fetched, added, _ = seen
    assert "J01636.1:lacZ" in fetched  # The handle the input was built from.
    assert "'count': 1" in added


async def test_agent_must_declare_an_input_before_it_can_submit(
    results_dir, monkeypatch
):
    monkeypatch.setattr(entrez, "_get", lambda *a, **k: LACZ)
    seen: list[str] = []
    calls = [
        CREATE_PROTEIN,
        ("fetch_sequences", {"accession": "J01636.1"}),
        (
            "add_input",
            {"name": "seq", "source": "NCBI J01636.1", "handles": ["J01636.1:lacZ"]},
        ),
    ]

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(str(p.content) for p in _returns(messages))
        turn = _turn(messages)
        if turn == 0:  # Submitting with nothing to run on.
            return _submit(info, GOOD)
        return _call(*calls[turn - 1]) if turn <= len(calls) else _submit(info, GOOD)

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    out = (await agent.run("build", deps=Hypothesis(goal="translate lacZ"))).output
    assert "This hypothesis has no inputs yet" in seen[0]
    assert out.inputs == {"seq": [Dna(sequence="ATGGCTCTGAAATAA")]}


async def test_add_input_rejects_what_it_cannot_stand_behind(results_dir):
    """A made-up sequence, an unknown handle, and overwriting what the caller gave."""
    seen: list[str] = []
    calls = [
        ("add_input", {"name": "given", "source": "mine", "sequences": ["ATG"]}),
        ("add_input", {"name": "seq", "source": "memory", "sequences": ["ATGXYZ"]}),
        ("add_input", {"name": "seq", "source": "a record", "handles": ["J01636.1"]}),
        CREATE_PROTEIN,
    ]

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(str(p.content) for p in _returns(messages))
        turn = _turn(messages)
        if turn < len(calls):
            return _call(*calls[turn])
        return _submit(
            info,
            {
                "inputs": {"given": "dna"},
                "steps": {"protein": _node(PROTEIN, "given")},
            },
        )

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    hyp = Hypothesis(goal="translate", inputs={"given": [Dna(sequence="ATG")]})
    out = (await agent.run("build", deps=hyp)).output

    assert "was given with the goal and cannot be replaced" in seen[0]
    assert "not valid dna" in seen[1]
    assert "No such handle: ['J01636.1']" in seen[2]
    # Nothing the agent tried stuck, so the run uses the caller's input alone.
    assert out.inputs == {"given": [Dna(sequence="ATG")]}
    assert out.input_sources == {}
