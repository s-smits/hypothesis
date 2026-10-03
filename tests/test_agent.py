
import json

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import ValidationError
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from node_dag.agent import Hypothesis, build_agent, criteria_agent
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.plan import Criterion
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.dag.activities import results_subdir
from temporal.hypothesis.activities import Stage, _ask, compaction, save_hypothesis
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
    from temporal.hypothesis.activities import Stage, _ask

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


SUMMARY = "The goal is to translate DNA to protein; no node is made yet."
PLAN = {
    "hypothesis": "convert DNA to protein",
    "expected": "proteins",
    **GOOD,
    "steps": {"protein": {**GOOD["steps"]["protein"], "why": "w"}},
}


def _stream(name: str, number: int, reply: dict) -> str:
    """``reply`` as the server-sent events the API streams it in, a compaction block as one delta."""
    usage = reply.get("usage", {"input_tokens": 1000, "output_tokens": 60})
    message = {
        "id": f"msg_{number}",
        "type": "message",
        "role": "assistant",
        "model": name,
    }
    events: list[dict] = [
        {
            "type": "message_start",
            "message": {**message, "content": [], "stop_reason": None, "usage": usage},
        }
    ]
    for i, block in enumerate(reply["content"]):
        if block["type"] == "compaction":
            start = {**block, "content": None}
            delta = {"type": "compaction_delta", "content": block["content"]}
        else:
            start = {**block, "input": {}}
            delta = {
                "type": "input_json_delta",
                "partial_json": json.dumps(block["input"]),
            }
        events += [
            {"type": "content_block_start", "index": i, "content_block": start},
            {"type": "content_block_delta", "index": i, "delta": delta},
            {"type": "content_block_stop", "index": i},
        ]
    stop = {"stop_reason": "tool_use", "stop_sequence": None}
    events += [
        {
            "type": "message_delta",
            "delta": stop,
            "usage": {"output_tokens": usage["output_tokens"]},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def _api(name: str, replies: list[dict]) -> tuple[AnthropicModel, list[httpx2.Request]]:
    """An Anthropic model whose API is scripted: each request is kept, and answered with the
    next of ``replies`` (its content, and its usage when it has one)."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = _stream(name, len(seen), replies[len(seen) - 1])
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    client = AsyncAnthropic(
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
    )
    return AnthropicModel(
        name, provider=AnthropicProvider(anthropic_client=client)
    ), seen


def _tool(name: str, args: dict) -> dict:
    return {"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": args}


async def _plan_call(name: str, replies: list[dict], results_dir):
    """Run the plan stage on a scripted API, as the activity does. Returns what it answered
    with, and the requests the API saw."""
    model, seen = _api(name, replies)
    stage = Stage(
        hyp=Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}, round=1),
        model=f"anthropic:{name}",
    )
    agent = build_agent(model, Registry(results_dir / "registry"))
    return await _ask(agent, "plan it", stage, "plan", deps=stage.hyp), seen, stage


def _compacting(summary: str) -> dict:
    """A reply the API compacted on its way: it read 150k tokens to write the summary."""
    summarised = {
        "input_tokens": 150_000,
        "output_tokens": 600,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    return {
        "content": [{"type": "compaction", "content": summary}, _tool(*CREATE_PROTEIN)],
        "usage": {
            "input_tokens": 900,
            "output_tokens": 40,
            "iterations": [
                {"type": "compaction", **summarised},
                {"type": "message", "input_tokens": 900, "output_tokens": 40},
            ],
        },
    }


async def test_a_long_builder_call_is_compacted_by_the_api_and_the_summary_is_kept(
    results_dir,
):
    replies = [_compacting(SUMMARY), {"content": [_tool("final_result", PLAN)]}]
    done, seen, stage = await _plan_call("claude-sonnet-5-5", replies, results_dir)

    first, second = (json.loads(r.content) for r in seen)
    edit = {
        "type": "compact_20260112",
        "trigger": {"type": "input_tokens", "value": 150_000},
    }
    assert first["context_management"] == {"edits": [edit]}
    assert "compact-2026-01-12" in seen[0].headers["anthropic-beta"]
    # The summary goes back first in the next request, in place of what it summarises.
    assert second["messages"][0]["content"][0] == {
        "type": "compaction",
        "content": SUMMARY,
    }
    assert done["out"].steps["protein"].node == PROTEIN
    # The tokens the API read to write the summary are spent, so they are counted.
    assert done["tokens"] == 150_000 + 600 + 900 + 40 + 1000 + 60
    saved = (results_dir / "trajectories" / f"{stage.hyp.id}-r1-plan.json").read_bytes()
    kept = [
        p
        for m in ModelMessagesTypeAdapter.validate_json(saved)
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, CompactionPart)
    ]
    assert [p.content for p in kept] == [SUMMARY]


@pytest.mark.parametrize(
    ("name", "window"),
    [
        ("claude-haiku-4-5", None),  # The API refuses compaction on it.
        ("claude-sonnet-5-5", "0"),  # Turned off.
    ],
)
async def test_a_call_that_cannot_compact_or_has_it_off_sends_what_it_always_did(
    results_dir, monkeypatch, name, window
):
    if window:
        monkeypatch.setenv("NODE_DAG_COMPACT_WINDOW", window)
    replies = [
        {"content": [_tool(*CREATE_PROTEIN)]},
        {"content": [_tool("final_result", PLAN)]},
    ]
    done, seen, _ = await _plan_call(name, replies, results_dir)

    assert done["out"].steps["protein"].node == PROTEIN
    for request in seen:
        assert "context_management" not in json.loads(request.content)
        assert "compact" not in request.headers.get("anthropic-beta", "")


def test_the_window_is_never_below_what_the_api_takes_and_only_anthropic_models_get_it(
    monkeypatch,
):
    (found,) = compaction("anthropic:claude-opus-5-5")
    assert found.token_threshold == 150_000
    assert compaction("test") == compaction("openai:gpt-5") == []
    assert compaction("anthropic:claude-haiku-4-5") == []
    assert compaction("anthropic:claude-sonnet-4-5") == []
    monkeypatch.setenv("NODE_DAG_COMPACT_WINDOW", "20000")
    assert compaction("anthropic:claude-sonnet-5-5")[0].token_threshold == 50_000


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


async def test_a_call_that_fails_still_counts_its_tokens():
    from pydantic_ai import Agent, ModelRetry
    from pydantic_ai.models.test import TestModel

    from temporal.hypothesis.activities import Stage, _ask

    stage = Stage(hyp=Hypothesis(goal="g", inputs={"seq": [Dna(sequence="ATG")]}), model="test")
    never = Agent(TestModel(), output_type=str, retries={"output": 0})

    @never.output_validator
    def refuse(out: str) -> str:
        raise ModelRetry("a guard said no")

    bad = await _ask(never, "plan it", stage, "plan")
    assert "error" in bad and bad["tokens"] > 0


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

    # Removing a codon finds the nodes that recode and count codons.
    codon_results = search_nodes(query="remove codon", input_type="dna")
    assert {"recode_codons", "codon_count"} <= {r["name"] for r in codon_results}

    # Translation
    trans_results = search_nodes(query="translate to protein")
    assert trans_results[0]["name"] == "dna_to_protein"
