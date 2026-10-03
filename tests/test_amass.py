import json

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from node_dag import amass
from node_dag.agent import Hypothesis, Seen, build_agent, cite
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.hypothesis import activities
from temporal.hypothesis.activities import Stage

HIT = {
    "amassId": "AMBC_1",
    "title": "Ribosome binding sites",
    "abstract": "...",
    "meshTerms": ["Ribosomes"],
}


@pytest.fixture
def calls(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE_DAG_RESULTS", str(tmp_path))
    made: list[tuple[str, list]] = []

    def fake_get(path, params):
        made.append((path, params))
        return HIT if "/records/" in path else [HIT]

    monkeypatch.setattr(amass, "_get", fake_get)
    return made


def test_search_is_cached_under_results_amass(calls, tmp_path):
    assert amass.search("biomedcore", "rbs", 3) == [HIT]
    assert amass.search("biomedcore", "rbs", 3) == [HIT]
    assert calls == [("/cores/biomedcore/records", [("query", "rbs"), ("limit", "3")])]
    (path,) = (tmp_path / "amass" / "biomedcore" / "search").iterdir()
    saved = json.loads(path.read_text())
    assert saved["request"] == {"query": "rbs", "limit": 3}


def test_a_different_query_is_a_new_call(calls):
    amass.search("biomedcore", "rbs")
    amass.search("biomedcore", "rbs", 10)
    amass.search("genecore", "rbs")
    assert len(calls) == 3


def test_get_record_is_cached_whatever_the_include_order(calls):
    amass.get_record("biomedcore", "AMBC_1", ("b", "a"))
    amass.get_record("biomedcore", "AMBC_1", ("a", "b"))
    assert calls == [
        ("/cores/biomedcore/records/AMBC_1", [("include", "a"), ("include", "b")])
    ]


def test_a_failure_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE_DAG_RESULTS", str(tmp_path))
    monkeypatch.delenv("AMASS_API_KEY", raising=False)
    with pytest.raises(amass.AmassError):
        amass.search("biomedcore", "rbs")
    assert not (tmp_path / "amass").exists()


def _builder(calls_: list, observations: list[dict]):
    """A builder that searches, then submits a plan citing ``observations``."""
    seen: list = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(
            p.content
            for p in messages[-1].parts
            if isinstance(p, ToolReturnPart | RetryPromptPart)
        )
        turn = sum(isinstance(m, ModelResponse) for m in messages)
        if turn < len(calls_):
            return ModelResponse(parts=[ToolCallPart(*calls_[turn])])
        args = {
            "hypothesis": "translate",
            "expected": "proteins",
            "inputs": {"seq": "dna"},
        }
        args["steps"] = {
            "protein": {"node": PROTEIN, "inputs": {"sequence": "seq"}, "why": "w"}
        }
        # Cite a made-up record first; the retry cites only the real one.
        cite = observations if turn == len(calls_) else observations[-1:]
        args["observations"] = cite
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return script, seen


PROTEIN = f"dna_to_protein__{DnaToProteinConfig().config_hash}"
CREATE = ("create_node", {"config": {"name": "dna_to_protein"}, "description": "x"})


async def test_builder_cites_only_records_it_was_shown(calls, tmp_path):
    script, seen = _builder(
        [("search_literature", {"query": "rbs"}), CREATE],
        [
            {"amass_id": "AMBC_made_up", "summary": "invented"},
            {"amass_id": "AMBC_1", "summary": "RBS strength sets expression."},
        ],
    )
    shown: Seen = {}
    agent = build_agent(FunctionModel(script), Registry(tmp_path / "registry"), shown)
    hyp = Hypothesis(goal="translate", inputs={"seq": [Dna(sequence="ATG")]})
    out = (await agent.run(hyp.goal, deps=hyp)).output

    hits, _, retry = seen
    assert hits == [
        {"amassId": "AMBC_1", "title": "Ribosome binding sites", "abstract": "..."}
    ]
    assert "AMBC_made_up" in str(retry)
    (obs,) = cite(out, shown)
    assert obs.model_dump() == {
        "amass_id": "AMBC_1",
        "summary": "RBS strength sets expression.",
        "core": "biomedcore",
        "title": "Ribosome binding sites",
        "url": None,
        "source": None,
        "date": None,
    }


async def test_the_plan_activity_returns_the_records_the_plan_cites(
    calls, tmp_path, monkeypatch
):
    script, _ = _builder(
        [("search_literature", {"query": "rbs"}), CREATE],
        [{"amass_id": "AMBC_1", "summary": "RBS strength sets expression."}],
    )
    real = activities.build_agent
    monkeypatch.setattr(
        activities,
        "build_agent",
        lambda model, registry, seen: real(FunctionModel(script), registry, seen),
    )
    hyp = Hypothesis(goal="translate", inputs={"seq": [Dna(sequence="ATG")]})

    out = await activities.plan_hypothesis(Stage(hyp=hyp, model="test"))

    assert out.error is None and out.plan
    assert [o.amass_id for o in out.plan.observations] == ["AMBC_1"]
    (obs,) = out.observations
    assert (obs.core, obs.title) == ("biomedcore", "Ribosome binding sites")
    assert obs.summary == "RBS strength sets expression."


async def test_a_failed_search_reaches_the_agent_as_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("NODE_DAG_RESULTS", str(tmp_path))
    monkeypatch.delenv("AMASS_API_KEY", raising=False)
    script, seen = _builder([("search_literature", {"query": "rbs"}), CREATE], [])
    shown: Seen = {}
    agent = build_agent(FunctionModel(script), Registry(tmp_path / "registry"), shown)
    hyp = Hypothesis(goal="translate", inputs={"seq": [Dna(sequence="ATG")]})
    out = (await agent.run(hyp.goal, deps=hyp)).output
    assert seen[0] == {"error": "AMASS_API_KEY is not set."}
    assert out.observations == [] and cite(out, shown) == []
