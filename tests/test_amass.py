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
from node_dag.agent import (
    Hypothesis,
    Observation,
    build_agent,
    observations_agent,
)
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.registry import Registry
from node_dag.types import Dna

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
    """A builder that searches, then submits a DAG citing ``observations``."""
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
        args = {"hypothesis": "translate", "inputs": {"seq": "dna"}, "steps": {}}
        args["steps"] = {"protein": {"node": PROTEIN, "inputs": {"sequence": "seq"}}}
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
    agent = build_agent(FunctionModel(script), Registry(tmp_path / "registry"))
    hyp = Hypothesis(goal="translate", inputs={"seq": [Dna(sequence="ATG")]})
    out = (await agent.run(hyp.goal, deps=hyp)).output

    hits, _, retry = seen
    assert hits == [
        {"amassId": "AMBC_1", "title": "Ribosome binding sites", "abstract": "..."}
    ]
    assert "AMBC_made_up" in str(retry)
    (obs,) = out.observations
    assert obs.model_dump() == {
        "amass_id": "AMBC_1",
        "summary": "RBS strength sets expression.",
        "core": "biomedcore",
        "title": "Ribosome binding sites",
        "url": None,
        "source": None,
        "date": None,
    }


async def test_a_failed_search_reaches_the_agent_as_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("NODE_DAG_RESULTS", str(tmp_path))
    monkeypatch.delenv("AMASS_API_KEY", raising=False)
    script, seen = _builder([("search_literature", {"query": "rbs"}), CREATE], [])
    agent = build_agent(FunctionModel(script), Registry(tmp_path / "registry"))
    hyp = Hypothesis(goal="translate", inputs={"seq": [Dna(sequence="ATG")]})
    out = (await agent.run(hyp.goal, deps=hyp)).output
    assert seen[0] == {"error": "AMASS_API_KEY is not set."}
    assert out.observations == []


GIVEN = Observation(
    amass_id="AMBC_given",
    summary="What the user kept.",
    core="biomedcore",
    title="Codon usage and expression",
    url="https://example.invalid/given",
)


def _given_hypothesis() -> Hypothesis:
    """A hypothesis carrying an observation gathered before the build."""
    return Hypothesis(
        goal="translate",
        observations=[GIVEN],
        inputs={"seq": [Dna(sequence="ATG")]},
    )


async def test_the_builder_cites_an_observation_it_was_given(tmp_path):
    """A record the agent did not fetch, but was shown, is its to cite."""
    script, _ = _builder(
        [CREATE], [{"amass_id": "AMBC_given", "summary": "Set the UTR."}]
    )
    agent = build_agent(FunctionModel(script), Registry(tmp_path / "registry"))
    hyp = _given_hypothesis()
    out = (await agent.run(hyp.goal, deps=hyp)).output

    # The builder's summary of how it used the record replaces the one it was
    # given, and the record's own title and link survive: it did not refetch it.
    (obs,) = out.observations
    assert obs == GIVEN.model_copy(update={"summary": "Set the UTR."})


async def test_an_observation_the_builder_does_not_cite_is_kept(tmp_path):
    """Gathering a record before the build is not undone by going uncited."""
    script, _ = _builder([CREATE], [])
    agent = build_agent(FunctionModel(script), Registry(tmp_path / "registry"))
    hyp = _given_hypothesis()
    out = (await agent.run(hyp.goal, deps=hyp)).output
    assert out.observations == [GIVEN]


async def test_a_cited_record_the_builder_found_is_added_after_the_given_ones(
    calls, tmp_path
):
    script, seen = _builder(
        [("search_literature", {"query": "rbs"}), CREATE],
        [
            {"amass_id": "AMBC_made_up", "summary": "invented"},
            {"amass_id": "AMBC_1", "summary": "RBS strength sets expression."},
        ],
    )
    agent = build_agent(FunctionModel(script), Registry(tmp_path / "registry"))
    hyp = _given_hypothesis()
    out = (await agent.run(hyp.goal, deps=hyp)).output

    # The retry lists both the record it was shown and the one it was given.
    retry = str(seen[-1])
    assert "AMBC_made_up" in retry
    assert "AMBC_1" in retry and "AMBC_given" in retry
    assert [o.amass_id for o in out.observations] == ["AMBC_given", "AMBC_1"]


def _observer(calls_: list, observations: list[dict]):
    """An observations agent that searches, then submits ``observations``."""

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn = sum(isinstance(m, ModelResponse) for m in messages)
        if turn < len(calls_):
            return ModelResponse(parts=[ToolCallPart(*calls_[turn])])
        # Cite a made-up record first; the retry cites only the real one.
        cite = observations if turn == len(calls_) else observations[-1:]
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"observations": cite})]
        )

    return script


async def test_the_observations_agent_returns_records_it_was_shown(calls):
    script = _observer(
        [("search_literature", {"query": "rbs"})],
        [
            {"amass_id": "AMBC_made_up", "summary": "invented"},
            {"amass_id": "AMBC_1", "summary": "RBS strength sets expression."},
        ],
    )
    out = (
        await observations_agent(FunctionModel(script)).run("Goal: express lacZ")
    ).output

    # The invented citation was rejected, so only the searched record comes back,
    # with the provenance the record itself carried.
    assert [o.model_dump() for o in out] == [
        {
            "amass_id": "AMBC_1",
            "summary": "RBS strength sets expression.",
            "core": "biomedcore",
            "title": "Ribosome binding sites",
            "url": None,
            "source": None,
            "date": None,
        }
    ]


async def test_the_observations_agent_may_find_nothing(calls):
    """An empty list is a usable answer: the search settled nothing."""
    script = _observer([("search_literature", {"query": "rbs"})], [])
    out = (
        await observations_agent(FunctionModel(script)).run("Goal: express lacZ")
    ).output
    assert out == []
    assert len(calls) == 1  # It did search before answering.
