import json
from datetime import UTC, datetime
from typing import Any

import pytest
from click.testing import CliRunner
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage
from test_guards import ASK, HYP, _plan

import node_dag.nodes
from node_dag.plan import Assertion, Attempt, Plan, ToolRequest
from temporal import pulse
from temporal.dag.activities import results_subdir
from temporal.hypothesis.activities import save_hypothesis
from temporal.hypothesis.loop import HypothesisInput
from temporal.pulse import (
    ALERTS,
    BLOCKED_S,
    BUDGET,
    FRICTION,
    Call,
    Memory,
    Reading,
    Round,
    events,
    held_text,
    load_memory,
    main,
    node_state,
    read_call,
    read_calls,
    read_round,
    read_run,
    render,
    save_memory,
    selected,
    status,
)

PLAN = Plan.model_validate(_plan())
NOW = 100_000.0


def reading(**kw) -> Reading:
    base: dict[str, Any] = {
        "id": "h1",
        "label": "h1",
        "goal": "remove TCG",
        "state": "building",
        "now": NOW,
        "saved": NOW,
        "started": NOW - 600,
        "round": 1,
        "budget": 500_000,
    }
    return Reading(**{**base, **kw})


def texts(before: Reading | None, after: Reading) -> list[str]:
    return [f"{e.mark} {e.text}" for e in events(before, after)]


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(NOW + seconds, UTC)


def transcript(retries: list, *, finished: bool = True) -> list:
    """A plan call that read two nodes, was sent back once per entry of ``retries``, and
    then answered, unless it ``finished`` is False."""
    usage = RequestUsage(input_tokens=100, output_tokens=10)
    called = lambda *names: ModelResponse(
        parts=[
            ToolCallPart(n, {}, tool_call_id=f"{n}{i}") for i, n in enumerate(names)
        ],
        usage=usage,
        model_name="m",
        timestamp=at(len(names)),
    )
    answer = ModelResponse(
        parts=[ToolCallPart("final_result", {}, tool_call_id="f")],
        usage=usage,
        model_name="m",
        timestamp=at(20),
    )
    messages = [
        ModelRequest(parts=[UserPromptPart("go", timestamp=at(0))]),
        called("list_nodes"),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "list_nodes", "ok", tool_call_id="list_nodes0", timestamp=at(2)
                )
            ]
        ),
        called("describe_node", "describe_node"),
    ]
    messages.append(answer)
    for why in retries:
        messages += [
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        why,
                        tool_name="final_result",
                        tool_call_id="f",
                        timestamp=at(10),
                    )
                ]
            ),
            answer,
        ]
    if finished:
        messages += [
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "final_result", "ok", tool_call_id="f", timestamp=at(30)
                    )
                ]
            )
        ]
    return messages


def write_transcript(hyp_id: str, number: int, stage: str, messages: list) -> None:
    path = results_subdir("trajectories") / f"{hyp_id}-r{number}-{stage}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ModelMessagesTypeAdapter.dump_json(messages))


def save(**fields) -> None:
    save_hypothesis(HYP.model_copy(update={"id": "h1", **fields}))


# --- A model call, from its transcript.


def test_a_call_shows_the_tools_read_the_retries_sent_and_whether_it_finished():
    bad = [
        {
            "type": "value_error",
            "loc": ("requests", "x"),
            "msg": "Unknown kind",
            "input": 1,
        }
    ]
    call = read_call(
        1,
        "plan",
        transcript([bad, "inputs must be exactly the goal's inputs\nmore"]),
        saved=5.0,
    )
    assert call.tools == {"list_nodes": 1, "describe_node": 2}
    assert call.retries == [
        "requests.x: Unknown kind",
        "inputs must be exactly the goal's inputs",
    ]
    assert (
        call.done and call.model == "m" and call.took == 30 and call.tokens == 110 * 5
    )
    assert not read_call(1, "plan", transcript([], finished=False), saved=5.0).done


def test_a_call_that_answers_in_plain_text_is_finished():
    """The criteria and critique stages return text and call no output tool."""
    usage = RequestUsage(input_tokens=100, output_tokens=10)
    text = [
        ModelRequest(parts=[UserPromptPart("go", timestamp=at(0))]),
        ModelResponse(
            parts=[TextPart("done")], usage=usage, model_name="m", timestamp=at(2)
        ),
    ]
    assert read_call(1, "critique", text, saved=5.0).done
    assert not read_call(1, "critique", text[:1], saved=5.0).done


def test_transcripts_are_read_per_run_and_a_run_whose_id_starts_another_is_not_mixed_in():
    write_transcript("run", 1, "plan", transcript([]))
    write_transcript("run", 0, "criteria", transcript([]))
    write_transcript("run-r2", 1, "plan", transcript([]))
    (results_subdir("trajectories") / "run-r1-plan.json.tmp").write_text("{")
    (results_subdir("trajectories") / "run-r3-verify.json").write_text("not json")
    assert [(c.round, c.stage) for c in read_calls("run")] == [
        (0, "criteria"),
        (1, "plan"),
    ]
    assert [(c.round, c.stage) for c in read_calls("run-r2")] == [(1, "plan")]


def test_a_call_with_many_retries_is_a_warning_that_says_what_it_was_sent_back_for():
    why = [f"reason {i}" for i in range(FRICTION)]
    call = read_call(1, "plan", transcript(why), saved=5.0)
    (event,) = [
        e for e in events(reading(), reading(calls=[call])) if "plan call" in e.text
    ]
    assert (
        event.mark == "⚠"
        and "sent back for: reason 0; reason 1; reason 2" in event.text
    )
    assert event.look == ["trajectories/h1-r1-plan.json"]
    one = read_call(1, "plan", transcript(["only one"]), saved=5.0)
    assert [e.mark for e in events(reading(), reading(calls=[one]))] == ["·"]


def test_a_call_already_seen_is_not_said_again_unless_its_transcript_was_rewritten():
    call = read_call(1, "plan", transcript([]), saved=5.0)
    assert events(reading(calls=[call]), reading(calls=[call])) == []
    again = call.model_copy(update={"saved": 9.0})
    assert len(events(reading(calls=[call]), reading(calls=[again]))) == 1


# --- What moved.


def test_the_first_reading_has_nothing_to_differ_from():
    assert events(None, reading(criteria=["a"], rounds=[Round(number=1)])) == []


def test_criteria_rounds_plans_assertions_verdicts_and_critiques_are_said_once_each():
    r1 = Round(
        number=1,
        steps=4,
        wiring="aa",
        requests=["gc"],
        held={"a": True, "b": False},
        achieved=False,
        reason="b failed",
        cause="wrong_config",
    )
    r2 = Round(number=2)
    assert texts(reading(), reading(criteria=["a", "b"])) == ["◆ criteria fixed: a, b"]
    after = reading(criteria=["a", "b"], rounds=[r1, r2], round=2)
    assert texts(reading(criteria=["a", "b"], rounds=[Round(number=1)]), after) == [
        "◆ r1 plan accepted: 4 steps, asking for gc",
        "◆ r1 assertions 1/2; did not hold: b",
        "◆ r1 missed: b failed",
        "◆ r1 critique: wrong_config",
        "◆ r2 opened; r1 missed (wrong_config)",
    ]
    assert events(after, after) == []


def shared_assertions(improved: bool) -> Attempt:
    """Arm E's round 2: two criteria both asserted ``produced`` on one filter."""
    ask = lambda c, step, branch: Assertion(
        criterion=c, step=step, branch=branch, claim="c"
    )
    plan = PLAN.model_copy(
        update={
            "assertions": [
                ask("protein_preserved", "protein_ok", "yes"),
                ask("length_preserved", "length_ok", "yes"),
                ask("higher_cai", "improved", "produced"),
                ask("only_improved_kept", "improved", "produced"),
            ]
        }
    )
    held = {
        "protein_ok.yes": True,
        "length_ok.yes": True,
        "improved.produced": improved,
    }
    return Attempt(round=1, plan=plan, held=held)


def test_two_assertions_on_one_step_and_branch_are_counted_twice():
    ok, bad = read_round(shared_assertions(True)), read_round(shared_assertions(False))
    assert (held_text(ok), held_text(bad)) == ("4/4", "2/4")
    assert "r1 4/4" in status(reading(rounds=[ok]), 0)
    said = texts(reading(rounds=[Round(number=1)]), reading(rounds=[bad]))
    assert "◆ r1 assertions 2/4; did not hold: improved.produced" in said


def test_a_round_that_errored_is_a_warning():
    said = texts(
        reading(rounds=[Round(number=1)]),
        reading(rounds=[Round(number=1, error="no valid plan")]),
    )
    assert said == ["⚠ r1 error: no valid plan"]


def test_blocking_waiting_on_nodes_and_resuming():
    blocked = reading(state="blocked", pending={"orf": "missing"})
    (event,) = events(reading(), blocked)
    assert (event.mark, event.text, event.look) == (
        "◆",
        "r1 blocked on orf",
        ["requests/orf.json"],
    )
    assert texts(blocked, reading(state="blocked", pending={"orf": "scaffolded"})) == [
        "· node orf: missing → scaffolded"
    ]
    ready = reading(state="blocked", pending={"orf": "ready"})
    assert texts(reading(state="blocked", pending={"orf": "unregistered"}), ready) == [
        "· node orf: unregistered → ready",
        "◆ every requested node is in place: restart the worker if it predates them, then Resume",
    ]
    assert texts(ready, reading(state="running")) == ["◆ r1 resumed"]


def test_a_run_that_ends_says_why_once():
    ended = reading(
        state="achieved",
        stopped="every criterion was covered by an assertion that held",
    )
    assert texts(reading(state="verifying"), ended) == [
        "◆ ended: achieved: every criterion was covered by an assertion that held"
    ]
    assert events(ended, ended) == []


# --- Alerts say once when they start and once when they clear.


def test_waiting_on_a_person_too_long_and_a_nearly_spent_budget_are_flagged():
    long = reading(
        state="blocked", pending={"a": "missing", "b": "missing"}, saved=NOW - BLOCKED_S
    )
    assert texts(
        reading(state="blocked", pending={"a": "missing", "b": "missing"}), long
    ) == ["⚠ blocked 30m00s on a, b"]
    assert texts(reading(tokens=350_000), reading(tokens=400_000)) == [
        "⚠ 400k of 500k tokens used"
    ]
    assert ALERTS["spent"](reading(tokens=400_000, state="achieved")) is None
    assert ALERTS["spent"](reading(tokens=400_000, budget=0)) is None


# --- Status.


def test_a_status_line_says_where_the_run_stands_in_the_terms_of_its_state():
    done = Round(number=1, held={"a": True, "b": False}, achieved=False)
    call = Call(
        round=1, stage="plan", retries=["x", "y"], began=0, took=1, done=True, saved=0
    )
    line = status(
        reading(
            criteria=["a", "b"],
            rounds=[done],
            calls=[call],
            tokens=74_000,
            saved=NOW - 130,
        ),
        8,
    )
    assert (
        line
        == "h1        10m00s  r1 building 2m10s: the plan call | rounds r1 1/2 · 74k/500k tokens · 2 guard retries"
    )
    assert "blocked 10s on a" in status(
        reading(state="blocked", pending={"a": "missing"}, saved=NOW - 10), 0
    )
    assert "achieved: it held" in status(
        reading(state="achieved", stopped="it held"), 0
    )


def test_a_blocked_run_says_what_each_requested_node_still_needs():
    waiting = reading(
        state="blocked",
        pending={"a": "missing", "b": "scaffolded", "c": "unregistered"},
    )
    assert pulse.next_step(waiting) == (
        "waiting on a (missing: python -m temporal.scaffold_node a), b (scaffolded: write its run()), "
        "c (unregistered: add it to factory.py)"
    )
    ready = pulse.next_step(reading(state="blocked", pending={"a": "ready"}))
    assert ready and ready.startswith("every requested node is in place")
    assert pulse.next_step(reading(pending={"a": "missing"})) is None


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    """A node tree of its own, so a node can be at each stage."""
    root = tmp_path / "src" / "node_dag"
    (root / "nodes" / "tools").mkdir(parents=True)
    (root / "nodes" / "filters").mkdir()
    (root / "factory.py").write_text("MAPPING = {AlphaConfig: Alpha}\n")
    monkeypatch.setattr(node_dag.nodes, "__file__", str(root / "nodes" / "__init__.py"))
    return root / "nodes"


def test_a_requested_node_is_missing_then_scaffolded_then_unregistered_then_ready(
    nodes,
):
    assert node_state("beta") == "missing"
    folder = nodes / "tools" / "beta"
    folder.mkdir()
    assert node_state("beta") == "scaffolded"  # No function.py yet.
    (folder / "function.py").write_text(
        'def run():\n    raise NotImplementedError("beta")\n'
    )
    assert node_state("beta") == "scaffolded"
    (folder / "function.py").write_text("def run():\n    return []\n")
    assert node_state("beta") == "unregistered"
    (nodes.parent / "factory.py").write_text("MAPPING = {BetaConfig: Beta}\n")
    assert node_state("beta") == "ready"
    (nodes / "filters" / "gamma").mkdir()
    assert node_state("gamma") == "scaffolded"


# --- Reading a run from its files.


def test_a_blocked_run_is_read_from_its_hypothesis_its_requests_and_its_transcripts(
    nodes,
):
    request = ToolRequest(name="gc_count", node="score", output=["gc"], **ASK)
    attempt = Attempt(round=1, plan=PLAN, requests=[request], started=at(-300))
    save(
        state="blocked",
        round=1,
        attempts=[attempt],
        usage={"total": 1234},
        stopped_because=None,
    )
    write_transcript("h1", 1, "plan", transcript(["why"]))
    r = read_run(results_subdir("hypotheses") / "h1.json", NOW, 500_000)
    assert isinstance(r, Reading)
    assert (r.id, r.state, r.round, r.tokens) == ("h1", "blocked", 1, 1234)
    assert r.criteria == ["no_tcg"] and r.pending == {"gc_count": "missing"}
    assert r.rounds[0].nodes == ["at_most", "codon_count"] and r.rounds[0].asked == []
    assert (
        r.rounds[0].requests == ["gc_count"]
        and r.rounds[0].steps == 2
        and r.rounds[0].wiring == PLAN.fingerprint()[:8]
    )
    assert r.started == NOW - 300 or r.started < NOW  # The earliest sign of the run.
    assert [c.stage for c in r.calls] == ["plan"]


def test_a_generated_id_is_shortened_and_a_chosen_one_is_not():
    assert (
        pulse.label_of("hypothesis-ce59ac5b-aa99-45e3-b92d-02d40a067a94") == "ce59ac5b"
    )
    assert pulse.label_of("recode-acg-mock") == "recode-acg-mock"


def test_a_file_that_is_not_a_hypothesis_is_reported_and_does_not_stop_the_look(
    results_dir,
):
    save(state="building")
    (results_subdir("hypotheses") / "broken.json").write_text("{not json")
    seen = pulse.look((), Memory(), 500_000, NOW)
    assert [r.id for r in seen.runs] == ["h1"]
    assert seen.unreadable == ["broken.json (not JSON)"]
    assert "unreadable: broken.json (not JSON)" in render(seen)[0]


def test_a_file_saved_before_a_node_changed_says_which_hash_no_longer_matches(
    results_dir,
):
    stale = json.loads(HYP.model_dump_json())
    stale["id"] = "old"
    stale["dag"] = {
        "inputs": {"seq": "dna"},
        "steps": {
            "count": {
                "config": {
                    "name": "codon_count",
                    "config_hash": "deadbeef",
                    "codons": ["TCG"],
                },
                "inputs": {"sequence": "seq"},
            }
        },
    }
    results_subdir("hypotheses").mkdir(parents=True, exist_ok=True)
    (results_subdir("hypotheses") / "old.json").write_text(json.dumps(stale))
    why = read_run(results_subdir("hypotheses") / "old.json", NOW, 500_000)
    assert isinstance(why, str) and "config_hash 'deadbeef' is not" in why
    assert ". " not in why and "Value error" not in why


def test_a_missing_file_is_named_not_raised(results_dir):
    why = read_run(results_subdir("hypotheses") / "gone.json", NOW, 500_000)
    assert isinstance(why, str) and why.startswith("cannot be read")


# --- Which runs a look reads, and what it keeps.


def test_open_runs_are_read_and_a_closed_one_only_if_kept_or_named():
    open_, closed, legacy = (
        reading(),
        reading(id="h2", label="h2", state="achieved"),
        reading(id="h3", label="h3", state="legacy"),
    )
    kept = frozenset({"h2"})
    assert [selected(r, (), frozenset()) for r in (open_, closed, legacy)] == [
        True,
        False,
        False,
    ]
    assert [selected(r, (), kept) for r in (open_, closed, legacy)] == [
        True,
        True,
        False,
    ]
    assert selected(closed, ("h2",), frozenset()) and selected(
        open_, ("TCG",), frozenset()
    )
    assert not selected(open_, ("h2",), frozenset())


def test_memory_survives_a_round_trip_and_an_unreadable_file_is_a_first_look(
    results_dir,
):
    path = results_dir / "pulse.json"
    memory = Memory(readings={"h1": reading()})
    assert save_memory(path, memory) is None and load_memory(path) == memory
    raw = json.loads(path.read_text())
    raw["readings"]["h1"]["worker"] = True  # A field an older version kept.
    path.write_text(json.dumps(raw))
    assert load_memory(path) == memory
    raw["readings"]["h2"] = {"id": "h2"}  # Written by another version.
    path.write_text(json.dumps(raw))
    assert load_memory(path) == Memory()
    path.write_text("[1, 2]")
    assert load_memory(path) == Memory()
    path.write_text("{")
    assert load_memory(path) == Memory()
    assert load_memory(results_dir / "none.json") == Memory()
    blocked = results_dir / "a" / "pulse.json"
    (results_dir / "a").write_text("a file, so its directory cannot be made")
    assert "could not keep" in (save_memory(blocked, memory) or "")


def test_only_the_transcripts_of_a_run_the_look_reads_are_parsed(
    results_dir, monkeypatch
):
    save(state="verifying")
    save_hypothesis(HYP.model_copy(update={"id": "h2", "state": "achieved"}))
    parsed: list[str] = []
    read = pulse.read_calls
    monkeypatch.setattr(pulse, "read_calls", lambda i: parsed.append(i) or read(i))
    pulse.look((), Memory(), 500_000, NOW)
    assert parsed == ["h1"]
    parsed.clear()
    pulse.look(("h2",), Memory(), 500_000, NOW)
    assert parsed == ["h2"]


def test_the_default_budget_is_the_loops():
    assert BUDGET == HypothesisInput.model_fields["max_tokens"].default


def test_a_run_that_ended_is_said_once_and_then_no_longer_watched(results_dir):
    save(state="verifying")
    memory = Memory()
    first = pulse.look((), memory, 500_000, NOW)
    assert (
        first.events == [] and "h1" in memory.readings
    )  # A first look says nothing moved.
    assert any("goal: remove TCG" in line for line in first.status)
    save(state="achieved", stopped_because="done")
    ended = pulse.look((), memory, 500_000, NOW)
    assert [e.text for e in ended.events] == [
        "ended: achieved: done"
    ] and memory.readings == {}
    assert pulse.look((), memory, 500_000, NOW).runs == []


# --- The command.


def test_a_look_that_finds_no_run_says_where_it_looked(results_dir):
    head = render(pulse.look((), Memory(), 500_000, NOW))[0]
    assert f"0 runs under {results_dir}" in head


def test_interrupting_a_repeating_look_ends_it_quietly(monkeypatch):
    def interrupt(seconds: float):
        raise KeyboardInterrupt

    monkeypatch.setattr(pulse.time, "sleep", interrupt)
    done = CliRunner().invoke(main, ["--every", "30"])
    assert done.exit_code == 0 and done.exception is None
    assert "0 runs" in done.output


def test_the_command_prints_status_on_a_first_look_and_events_on_the_next():
    save(state="building", round=1)
    first = CliRunner().invoke(main, [])
    assert first.exit_code == 0 and "1 run" in first.output
    assert "◆" not in first.output and "building" in first.output
    request = ToolRequest(name="gc_count", node="score", output=["gc"], **ASK)
    save(
        state="blocked",
        round=1,
        attempts=[Attempt(round=1, plan=PLAN, requests=[request])],
    )
    second = CliRunner().invoke(main, [])
    assert (
        "◆ h1 r1 plan accepted: 2 steps, asking for gc_count → hypotheses/h1.json"
        in second.output
    )
    assert "◆ h1 r1 blocked on gc_count → requests/gc_count.json" in second.output
    assert (
        "↳ waiting on gc_count (missing: python -m temporal.scaffold_node gc_count)"
        in second.output
    )
    as_json = json.loads(CliRunner().invoke(main, ["--json", "h1"]).output)
    assert as_json["runs"][0]["state"] == "blocked" and as_json["events"] == []
    assert "no run" not in CliRunner().invoke(main, ["nothing-matches"]).output
    assert (
        CliRunner().invoke(main, ["nothing-matches"]).output.startswith(as_json["at"])
    )
