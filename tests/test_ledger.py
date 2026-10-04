import json

import pytest
from temporalio.exceptions import ApplicationError
from test_guards import ASK
from test_pulse import NOW, PLAN, at, reading, save, transcript, write_transcript

from node_dag.plan import Attempt, ToolRequest, Verdict
from temporal.dag.activities import results_subdir
from temporal.ledger import Entry, append, entry_for, ledger_path, read, record_ledger
from temporal.pulse import Call, Round


def call(stage: str, *retries: str, model: str = "m") -> Call:
    return Call(
        round=1,
        stage=stage,
        model=model,
        retries=list(retries),
        began=NOW,
        took=1,
        done=True,
        saved=NOW,
    )


def closed(**kw) -> Entry:
    rounds = [
        Round(number=1, wiring="aaaa", nodes=["a", "b"], asked=["gc_count"],
              held={"s.yes": False, "t.yes": True}, achieved=False),
        Round(number=2, wiring="bbbb", nodes=["a", "gc_count"],
              held={"s.yes": True, "t.yes": True}, achieved=True),
    ]  # fmt: skip
    return entry_for(
        reading(
            state="achieved",
            stopped="every criterion held",
            rounds=rounds,
            calls=[
                call("plan", "why one", "why two", model="big"),
                call("verify", model="small"),
            ],
            tokens=75_000,
            started=NOW - 300,
            saved=NOW,
            **kw,
        )
    )


def test_a_line_says_how_the_run_went_in_the_terms_pulse_reads_it():
    e = closed()
    assert (e.state, e.rounds, e.held, e.tokens, e.seconds) == (
        "achieved",
        2,
        ["1/2", "2/2"],
        75_000,
        300.0,
    )
    assert (e.retries, e.errors, e.repeats) == (2, 0, 0)
    assert (e.nodes, e.asked) == (["a", "b", "gc_count"], ["gc_count"])
    assert e.models == {"plan": "big", "verify": "small"}
    assert e.summary == "achieved in 2 rounds: every criterion held; asked for gc_count"
    assert e.run == f"h1@{(int(NOW) - 300) * 1000}" and e.hypothesis == "h1"


def test_a_repeated_wiring_a_failed_round_and_a_run_that_never_planned_are_counted():
    rounds = [
        Round(number=1, wiring="aaaa", error="boom"),
        Round(number=2, wiring="aaaa", held={"s.yes": False}),
        Round(number=3),
    ]
    e = entry_for(reading(state="not achieved", stopped="out of rounds", rounds=rounds))
    assert (e.repeats, e.errors, e.held) == (1, 1, ["error", "0/1", "-"])
    one = entry_for(reading(state="failed", stopped="no plan", rounds=rounds[:1]))
    assert one.summary == "failed in 1 round: no plan"  # Singular.


def test_a_line_is_added_once_and_a_line_that_no_longer_reads_is_counted_not_fatal(
    results_dir,
):
    e = closed()
    assert append(ledger_path(), e) and not append(ledger_path(), e)
    other = e.model_copy(update={"run": "h2@1", "hypothesis": "h2"})
    assert append(ledger_path(), other)
    with ledger_path().open("a") as f:
        f.write('{"run": "old"}\n\n')
    entries, bad = read(ledger_path())
    assert [x.hypothesis for x in entries] == ["h1", "h2"] and bad == 1
    assert read(results_dir / "none.jsonl") == ([], 0)


def test_the_activity_reads_a_saved_run_as_pulse_does_and_a_second_call_adds_nothing(
    results_dir,
):
    request = ToolRequest(name="gc_count", node="score", output=["gc"], **ASK)
    plan = PLAN.model_copy(update={"requests": {"gc_count": request}})
    verdict = Verdict(achieved=True, reason="r")
    save(
        state="achieved",
        round=1,
        stopped_because="every criterion held",
        usage={"total": 1234},
        attempts=[
            Attempt(
                round=1,
                plan=plan,
                held={"small.yes": True},
                verdict=verdict,
                started=at(-300),
            )
        ],
    )
    write_transcript("h1", 1, "plan", transcript(["too long", "bad kind"]))
    record_ledger("h1")
    record_ledger("h1")
    (e,), bad = read(ledger_path())
    assert bad == 0 and (e.state, e.tokens, e.retries) == ("achieved", 1234, 2)
    assert (e.held, e.nodes, e.asked) == (
        ["1/1"],
        ["at_most", "codon_count"],
        ["gc_count"],
    )
    assert json.loads(ledger_path().read_text())["hypothesis"] == "h1"


def test_a_run_that_cannot_be_read_is_an_error_the_loop_will_not_retry(results_dir):
    results_subdir("hypotheses").mkdir(parents=True, exist_ok=True)
    (results_subdir("hypotheses") / "broken.json").write_text("{not json")
    with pytest.raises(ApplicationError, match="broken cannot be read: not JSON") as e:
        record_ledger("broken")
    assert e.value.non_retryable and not ledger_path().exists()
