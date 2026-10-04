"""What the verifier and critic are sent, and how much of it one call may carry.

A live run once sent the verifier a 673k-character prompt (388,463 tokens, over the 200k
window): ten tables each repeating the same 22 three-kilobase sequences.
"""

import json

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_core import to_json
from test_loop import DAG, HYP, PLAN

from node_dag.agent import critique_agent, verify_agent
from node_dag.dag import DagOutput
from node_dag.plan import Attempt, Critique, VerifyOpinion
from node_dag.types import Dna, Table
from temporal.hypothesis import activities
from temporal.hypothesis.activities import (
    MAX_SHARDS,
    SHOWN,
    Stage,
    _view,
    _views,
    critique_attempt,
    verify_outcome,
)


def _dna(n: int, length: int = 3000) -> Dna:
    """A sequence unlike any other ``n``, ``length`` long."""
    unit = "".join("ACGT"[(n >> 2 * k) & 3] for k in range(8))
    return Dna(sequence=(unit * length)[:length])


def _judged(pool: list[Dna], tables: int = 3):
    """A round whose ``tables`` tables each hold the whole pool, with one score each."""
    score = {i.id: n / len(pool) for n, i in enumerate(pool)}
    out = DagOutput(
        values={
            f"t{k}": Table(items=pool, scores={"cai": dict(score)})
            for k in range(tables)
        },
        skipped=[],
    )
    attempt = Attempt(round=1, plan=PLAN, dag=DAG, outcome=out)
    return HYP.model_copy(update={"attempts": [attempt]})


def test_an_outcome_that_fits_is_sent_whole_and_unchanged():
    judged = _judged([_dna(n, 30) for n in range(4)])
    assert _views(judged) == [_view(judged)]


def test_the_same_entity_in_many_tables_is_sent_once(monkeypatch):
    judged = _judged([_dna(n) for n in range(22)], tables=10)
    assert (
        len(to_json(_view(judged))) > activities.PROMPT_CHARS
    )  # The failing run's shape.

    (only,) = _views(judged)

    assert len(to_json(only)) <= activities.PROMPT_CHARS
    out = only["outcome"]
    assert len(out["entities"]) == 22
    assert all(len(t["ids"]) == 22 for t in out["tables"].values())
    # A long sequence shows its two ends and its length, never the middle.
    seq = next(iter(out["entities"].values()))["sequence"]
    assert f"({3000} long)" in seq and "..." in seq and len(seq) < SHOWN + 30
    # Each table says how big it is and where its scores lie over every entity.
    assert out["tables"]["t0"]["count"] == 22
    assert out["tables"]["t0"]["ranges"]["cai"]["max"] == pytest.approx(21 / 22)
    assert "chunk" not in only
    # What the judge reasons from is all still there.
    assert only["assertions"] == _view(judged)["assertions"] and only["dag"]


def test_a_short_sequence_is_shown_whole(monkeypatch):
    monkeypatch.setattr(activities, "PROMPT_CHARS", 5_000)
    judged = _judged([_dna(n, SHOWN) for n in range(8)], tables=4)
    (only,) = _views(judged)
    assert {e["sequence"] for e in only["outcome"]["entities"].values()} == {
        i.sequence for i in judged.attempts[0].outcome.values["t0"].items
    }


def test_an_outcome_too_large_for_one_call_is_shared_out_with_every_call_seeing_the_totals(
    monkeypatch,
):
    monkeypatch.setattr(activities, "PROMPT_CHARS", 60_000)
    pool = [_dna(n) for n in range(300)]
    judged = _judged(pool, tables=2)

    views = _views(judged)

    assert 1 < len(views) <= MAX_SHARDS
    assert all(len(to_json(v)) <= activities.PROMPT_CHARS for v in views)
    seen = [e for v in views for e in v["outcome"]["entities"]]
    assert len(seen) == len(set(seen)) == 300  # Every entity once, none left out.
    for n, v in enumerate(views, 1):
        assert v["chunk"]["share"] == n and v["chunk"]["of"] == len(views)
        assert v["chunk"]["entities"] == 300 and v["chunk"]["every"] == 1
        assert (
            v["assertions"] == _view(judged)["assertions"]
        )  # Every call has the plan.
        t = v["outcome"]["tables"]["t0"]
        assert t["count"] == 300 and t["ranges"]["cai"]["n"] == 300  # And the totals.
        assert set(t["ids"]) == set(
            v["outcome"]["entities"]
        )  # But only its share's rows.
        assert set(t["scores"]["cai"]) == set(t["ids"])


def test_a_pool_beyond_what_the_calls_hold_is_sampled_and_says_so():
    judged = _judged([_dna(n) for n in range(1500)], tables=2)

    views = _views(judged)

    assert len(views) <= MAX_SHARDS
    assert all(len(to_json(v)) <= activities.PROMPT_CHARS for v in views)
    chunk = views[0]["chunk"]
    assert chunk["every"] > 1 and chunk["entities"] == 1500
    assert chunk["listed"] == sum(len(v["outcome"]["entities"]) for v in views) < 1500
    assert views[0]["outcome"]["tables"]["t0"]["count"] == 1500


def _verifier(monkeypatch, says: list[dict]):
    """Make ``verify_outcome`` use a model that answers each share from ``says``."""
    sent: list[dict] = []
    real = verify_agent

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            p.content for p in messages[0].parts if isinstance(p, UserPromptPart)
        )
        sent.append(json.loads(str(prompt)))
        # The shares are asked together, so each is answered by the share it was sent.
        got = says[sent[-1].get("chunk", {"share": 1})["share"] - 1]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, got)])

    monkeypatch.setattr(
        activities, "verify_agent", lambda model: real(FunctionModel(script))
    )
    return sent


def _small_calls(monkeypatch):
    monkeypatch.setattr(activities, "PROMPT_CHARS", 8_000)
    return _judged([_dna(n) for n in range(40)], tables=2)


AGREE = {"agrees": True, "covers_goal": True, "reason": "fine"}


async def test_a_veto_in_any_share_vetoes_the_round_and_names_the_share(monkeypatch):
    judged = _small_calls(monkeypatch)
    n = len(_views(judged))
    assert n > 1
    says = [AGREE] * n
    says[1] = {
        "agrees": False,
        "covers_goal": True,
        "reason": "entity x scores below the bar",
    }
    sent = _verifier(monkeypatch, says)

    out = await verify_outcome(Stage(hyp=judged, model="test"))

    assert len(sent) == n and out.error is None
    assert out.opinion == VerifyOpinion(
        agrees=False,
        covers_goal=True,
        reason=f"[share 2 of {n}] entity x scores below the bar",
    )
    assert out.tokens > 0


async def test_the_goal_is_covered_only_if_every_share_says_so(monkeypatch):
    judged = _small_calls(monkeypatch)
    n = len(_views(judged))
    says = [AGREE] * n
    says[-1] = {
        "agrees": True,
        "covers_goal": False,
        "reason": "no assertion checks the bar",
    }
    _verifier(monkeypatch, says)

    out = await verify_outcome(Stage(hyp=judged, model="test"))

    assert out.opinion is not None
    assert out.opinion.agrees and not out.opinion.covers_goal
    assert "no assertion checks the bar" in out.opinion.reason


async def test_every_share_agreeing_does_not_veto(monkeypatch):
    judged = _small_calls(monkeypatch)
    _verifier(monkeypatch, [AGREE] * len(_views(judged)))

    out = await verify_outcome(Stage(hyp=judged, model="test"))

    assert out.opinion is not None and out.opinion.agrees and out.opinion.covers_goal


async def test_a_round_that_fits_is_one_call_and_its_opinion_is_returned_as_given(
    monkeypatch,
):
    judged = _judged([_dna(n, 30) for n in range(3)])
    sent = _verifier(monkeypatch, [AGREE])

    out = await verify_outcome(Stage(hyp=judged, model="test"))

    assert sent == [
        json.loads(to_json(_view(judged)))
    ]  # The prompt is the old one, byte for byte.
    assert out.opinion == VerifyOpinion.model_validate(AGREE)


async def test_the_critic_gets_a_prompt_that_fits_and_says_it_is_a_share(monkeypatch):
    judged = _small_calls(monkeypatch)
    judged.attempts[-1].verdict = None
    sent: list[dict] = []
    real = critique_agent

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(
            json.loads(
                str(
                    next(
                        p.content
                        for p in messages[0].parts
                        if isinstance(p, UserPromptPart)
                    )
                )
            )
        )
        crit = Critique(
            diagnosis="d", root_cause="wrong_config", evidence=["e"], fix="f"
        )
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, crit.model_dump())]
        )

    monkeypatch.setattr(
        activities, "critique_agent", lambda model: real(FunctionModel(script))
    )

    out = await critique_attempt(Stage(hyp=judged, model="test"))

    assert out.critique is not None
    (prompt,) = sent
    assert prompt["chunk"]["share"] == 1 and prompt["chunk"]["of"] > 1
    assert len(to_json(prompt)) <= activities.PROMPT_CHARS
