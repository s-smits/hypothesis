

import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from node_dag import entrez
from node_dag.agent import (
    FoundInputs,
    Hypothesis,
    build_agent,
    criteria_agent,
    inputs_agent,
)
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.plan import Criterion
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.dag.activities import results_subdir
from temporal.hypothesis.activities import REQUEST_TIMEOUT, Stage, _ask, save_hypothesis
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
    steps = {k: {**v, "why": "w"} for k, v in dag["steps"].items()}
    plan = {"hypothesis": "convert DNA to protein", "expected": "proteins", **dag, "steps": steps}
    return _reply(info, plan)


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

    assert {k: s.node for k, s in out.steps.items()} == {"protein": PROTEIN}
    assert out.hypothesis == "convert DNA to protein"
    listing, registry, schema, created, error = seen
    assert '"inputs": {"sequence": "dna"}' in listing
    assert registry == "[]"  # Nothing made yet.
    assert "'x-node'" in schema  # The ports reach the model with the schema.
    assert PROTEIN in created
    assert "port 'sequence' takes Dna, but 'protein' gives AminoAcidSequence" in error


async def test_create_node_takes_a_config_sent_as_a_json_string(results_dir):
    """Recorded: E 6148f02d and F2 894a1eba sent it so; each was a full-context retry."""
    seen: list[ModelMessage] = []
    schema: list[dict] = []
    as_text = {**CREATE_PROTEIN[1], "config": json.dumps(CREATE_PROTEIN[1]["config"])}

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(messages)
        if _turn(messages) == 0:
            schema.extend(t.parameters_json_schema for t in info.function_tools)
            return _call("create_node", as_text)
        return _submit(info, GOOD)

    agent = build_agent(FunctionModel(script), Registry(results_dir / "registry"))
    hyp = Hypothesis(goal="translate", inputs={"seq": [Dna(sequence="ATG")]})
    out = (await agent.run(hyp.goal, deps=hyp)).output

    assert out.steps["protein"].node == PROTEIN
    assert not [p for m in seen for p in m.parts if isinstance(p, RetryPromptPart)]
    # The model is still told it is an object.
    (create,) = [s for s in schema if "description" in s["properties"]]
    assert create["properties"]["config"]["type"] == "object"


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
    assert out.steps["protein"].node == PROTEIN
    assert "dna_to_protein__deadbeef" in errors[0]


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
    assert out.steps["scored"].node == SCORER
    assert out.steps["small"].node.startswith("at_most__")


def test_a_hypothesis_without_a_dag_is_building():
    hyp = Hypothesis(
        goal="Convert DNA to protein.", inputs={"seq": [Dna(sequence="ATG")]}
    )
    save_hypothesis(hyp)
    (path,) = results_subdir("hypotheses").iterdir()
    assert _hypothesis_row(path).status == "building"


def test_goals_are_distinct_newest_first_with_latest_inputs():
    import os
    import time

    from temporal.ui.app import _goals

    for goal, seq in [("a", "ATG"), ("b", "AAA"), ("a", "TTT")]:
        save_hypothesis(Hypothesis(goal=goal, inputs={"seq": [Dna(sequence=seq)]}))
        time.sleep(0.01)
    paths = sorted(results_subdir("hypotheses").iterdir(), key=lambda p: p.stat().st_mtime)
    for i, p in enumerate(paths):  # mtimes can tie on coarse filesystems.
        os.utime(p, (1000 + i, 1000 + i))
    goals = _goals([_hypothesis_row(p) for p in paths])
    assert [(g.goal, g.hypotheses) for g in goals] == [("a", 2), ("b", 1)]
    assert goals[0].inputs == {"seq": [Dna(sequence="TTT")]}


def test_a_hypothesis_from_an_older_schema_is_skipped_not_fatal():
    """A file written before a schema change must not take out the whole listing."""
    from temporal.ui.app import _saved_rows

    save_hypothesis(Hypothesis(goal="good", inputs={"seq": [Dna(sequence="ATG")]}))
    stale = results_subdir("hypotheses") / "stale.json"
    # inputs.seq was a bare entity before it became a list.
    stale.write_text(
        '{"id": "old", "goal": "old", "inputs": {"seq": {"kind": "dna", "sequence": "ATG"}}}'
    )

    rows = _saved_rows()
    assert [r.hypothesis.goal for r in rows] == ["good"]


async def test_each_model_call_leaves_its_transcript_even_when_it_fails(results_dir):
    from pydantic_ai import Agent, ModelRetry
    from pydantic_ai.models.test import TestModel

    from node_dag.agent import Hypothesis
    from node_dag.types import Dna

    stage = Stage(hyp=Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}, round=2), model="test")
    ok = await _ask(Agent(TestModel(), output_type=str), "hello", stage, "verify")
    assert ok["out"] and ok["tokens"] > 0
    assert "hello" in (results_dir / "trajectories" / f"{stage.hyp.id}-r2-verify.json").read_text()

    never = Agent(TestModel(), output_type=str, retries={"output": 0})

    @never.output_validator
    def refuse(out: str) -> str:
        raise ModelRetry("a guard said no")

    bad = await _ask(never, "plan it", stage, "plan")
    assert "error" in bad and "plan it" in (results_dir / "trajectories" / f"{stage.hyp.id}-r2-plan.json").read_text()


def test_a_hypothesis_refuses_criteria_that_share_an_id():
    twins = [Criterion(id="no_tcg", claim="no TCG remains"), Criterion(id="no_tcg", claim="no TCA remains")]
    with pytest.raises(ValidationError, match="no_tcg"):
        Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}, criteria=twins)


async def test_derived_criteria_that_share_an_id_are_sent_back():
    replies = iter([
        [{"id": "no_tcg", "claim": "no TCG remains"}, {"id": "no_tcg", "claim": "no TCA remains"}],
        [{"id": "no_tcg", "claim": "no TCG remains"}, {"id": "no_tca", "claim": "no TCA remains"}],
    ])

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _reply(info, {"response": next(replies)})

    done = await criteria_agent(FunctionModel(script)).run("goal")
    assert [c.id for c in done.output] == ["no_tcg", "no_tca"]


async def test_criteria_the_agent_derives_are_marked_derived(results_dir, monkeypatch):
    from temporal.hypothesis.activities import derive_criteria

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _reply(info, {"response": [{"id": "no_tcg", "claim": "no TCG remains"}]})

    monkeypatch.setattr(
        "temporal.hypothesis.activities.criteria_agent",
        lambda model: criteria_agent(FunctionModel(script)),
    )
    hyp = Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]})
    out = await derive_criteria(Stage(hyp=hyp, model="test"))
    assert [(c.id, c.source) for c in out.criteria] == [("no_tcg", "derived")]


async def test_a_refusal_is_reported_as_declined_with_the_short_message(results_dir):
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ContentFilterError

    def refuse(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ContentFilterError("the response was filtered", body='{"big": "body"}')

    stage = Stage(hyp=Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}), model="test")
    out = await _ask(Agent(FunctionModel(refuse), output_type=str), "p", stage, "plan")
    assert out["declined"] and out["error"] == "the response was filtered"


async def test_a_call_that_fails_still_counts_its_tokens():
    from pydantic_ai import Agent, ModelRetry
    from pydantic_ai.models.test import TestModel


    stage = Stage(hyp=Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}), model="test")
    never = Agent(TestModel(), output_type=str, retries={"output": 0})

    @never.output_validator
    def refuse(out: str) -> str:
        raise ModelRetry("a guard said no")

    bad = await _ask(never, "plan it", stage, "plan")
    assert "error" in bad and bad["tokens"] > 0


async def test_a_request_the_api_never_answers_is_given_up_and_retried_by_the_client(
    results_dir, monkeypatch
):
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelAPIError
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    seen: list[asyncio.StreamWriter] = []

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        seen.append(writer)  # One per connection: a retry opens a new one.
        while await reader.read(65536):  # Take the request, and never answer it.
            pass
        writer.close()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)  # Any free port.
    port = server.sockets[0].getsockname()[1]
    provider = AnthropicProvider(api_key="not-a-key", base_url=f"http://127.0.0.1:{port}")
    agent = Agent(AnthropicModel("claude-sonnet-5-5", provider=provider), output_type=str)
    monkeypatch.setattr("temporal.hypothesis.activities.REQUEST_TIMEOUT", 1)
    stage = Stage(hyp=Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}), model="test")
    try:
        # Without the timeout the client waits 600 s, and this bound fails the test instead.
        with pytest.raises(ModelAPIError):
            await asyncio.wait_for(_ask(agent, "plan it", stage, "plan"), 30)
        assert len(seen) > 1  # The client's own retries ran, inside the activity's 10 minutes.
    finally:
        for writer in seen:
            writer.close()
        server.close()
        await asyncio.wait_for(provider.client.close(), 5)
        await asyncio.wait_for(server.wait_closed(), 5)


async def test_a_request_keeps_the_clients_five_second_connect_limit(results_dir):
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    class Probe:
        def __init__(self):
            self.settings: dict = {}

        async def run(self, prompt, usage, model_settings, **kw):
            self.settings = model_settings
            raise UnexpectedModelBehavior("stop here")

    probe = Probe()
    stage = Stage(hyp=Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}), model="test")
    await _ask(probe, "plan it", stage, "plan")
    timeout = probe.settings["timeout"]
    assert (timeout.connect, timeout.read) == (5.0, REQUEST_TIMEOUT)


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

    # Removing a codon finds the nodes that recode and count codons.
    codon_results = search_nodes(query="remove codon", input_type="dna")
    assert {"recode_targeted", "codon_count"} <= {r["name"] for r in codon_results}

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
                "expected": "a score",
                "inputs": {"seq": "dna"},
                "steps": {
                    "scored": {
                        "node": scorer_id,
                        "inputs": {"sequence": "seq"},
                        "why": "score it",
                    }
                },
                "assertions": [
                    {
                        "criterion": "up",
                        "step": "scored",
                        "branch": "produced",
                        "claim": "scored",
                    }
                ],
            },
        )

    agent = build_agent(FunctionModel(builder), Registry(results_dir))
    hyp = Hypothesis(
        goal="increase the expression of the sequence",
        inputs={"seq": [REF]},
        criteria=[Criterion(id="up", claim="expression rises")],
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
                "expected": "all pass",
                "inputs": {"seq": "dna"},
                "steps": {
                    "filt": {
                        "node": filter_id,
                        "inputs": {"items": "seq"},
                        "why": "keep the better ones",
                    }
                },
                "assertions": [
                    {
                        "criterion": "up",
                        "step": "filt",
                        "branch": "yes",
                        "claim": "kept",
                    }
                ],
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
        criteria=[Criterion(id="up", claim="expression rises")],
    )
    with pytest.raises(Exception):
        await agent.run("build", deps=hyp)

    assert any("allows every sequence to pass trivially" in err for err in seen_errors)


LACZ = """\
>lcl|J01636.1_cds_AAB59138.1_1 [gene=lacZ] [protein=beta-D-galactosidase] [location=1..15]
ATGGCTCTGAAATAA
"""


async def _find(calls: list[tuple[str, dict]]) -> tuple[list[str], FoundInputs]:
    """Run the inputs agent on a script of tool calls, and say what each call answered."""
    seen: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(str(p.content) for p in _returns(messages))
        turn = _turn(messages)
        if turn < len(calls):
            return _call(*calls[turn])
        return ModelResponse(parts=[TextPart("done")])

    agent, found = inputs_agent(FunctionModel(script))
    await agent.run("Goal: translate the E. coli lacZ CDS")
    return seen, found


async def test_the_inputs_agent_finds_a_record_and_adds_its_cds(
    results_dir, monkeypatch
):
    monkeypatch.setattr(entrez, "_get", lambda *a, **k: LACZ)
    monkeypatch.setattr(entrez, "search", lambda *a, **k: [{"accession": "J01636.1"}])
    add = {
        "name": "seq",
        "source": "NCBI J01636.1 CDS lacZ",
        "handles": ["J01636.1:lacZ"],
    }
    seen, found = await _find(
        [
            ("search_sequences", {"term": "lacZ[gene]"}),
            ("fetch_sequences", {"accession": "J01636.1", "gene": "lacZ"}),
            ("add_input", add),
        ]
    )
    assert found.inputs == {"seq": [Dna(sequence="ATGGCTCTGAAATAA")]}
    assert found.sources == {"seq": "NCBI J01636.1 CDS lacZ"}
    assert "J01636.1:lacZ" in seen[1] and "'count': 1" in seen[2]


async def test_add_input_rejects_what_it_cannot_stand_behind(results_dir):
    """A made-up sequence, a handle nobody fetched, a dotted name, and an empty input."""
    seen, found = await _find(
        [
            ("add_input", {"name": "seq", "source": "memory", "sequences": ["ATGXYZ"]}),
            ("add_input", {"name": "seq", "source": "a record", "handles": ["J01636.1"]}),
            ("add_input", {"name": "a.b", "source": "goal", "sequences": ["ATG"]}),
        ]
    )
    assert "not valid dna" in seen[0]
    assert "No such handle: ['J01636.1']" in seen[1]
    assert "no dots" in seen[2]
    assert found.inputs == {} and found.sources == {}
    empty, _ = await _find([("add_input", {"name": "seq", "source": "nothing"})])
    assert "at least one handle or sequence" in empty[0]
