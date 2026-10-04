import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_core import to_jsonable_python

import temporal.skills as skills_cli
from node_dag.agent import build_instructions
from node_dag.skills import SEED, Evolution
from temporal.skills import (
    KNOWN_SINCE,
    Checked,
    clip,
    judge,
    label_of,
    main,
    read_rounds,
    read_trails,
    render,
    trail,
)

HID = "hypothesis-26acc0f5-a73c-4f1d-8b61-6fe156ec9d46"
SEQ = "ATGGCTCTGAAATAA"


def _checked(
    accepted: bool = True,
    score: str = "PASS",
    stop: str = "ok",
    gate: str = "-",
    rnd: int = 1,
    hid: str = HID,
) -> Checked:
    return Checked(
        hypothesis_id=hid,
        round=rnd,
        accepted_by_loop=accepted,
        score_check=score,
        stop_check=stop,
        gate=gate,
        evidence="score PASS: 0/3 kept not above first input's 2 in expression",
    )


@pytest.mark.parametrize(
    ("accepted", "score", "stop", "gate", "label"),
    [
        (True, "FAIL", "ok", "-", "fail"),
        (False, "FAIL", "FAIL", "-", "fail"),
        (True, "EMPTY", "n/a", "-", "fail"),
        (True, "PASS", "ok", "PASS", "pass"),
        (True, "PASS", "n/a", "-", "pass"),
        (False, "PASS", "ok", "PASS", "pass"),
        (False, "PASS", "FAIL", "-", "fail"),
        (False, "PASS", "ok", "FAIL", "fail"),
        (True, "PASS", "FAIL", "-", None),
        (True, "PASS", "ok", "FAIL", None),
        (True, "PASS", "unmeasurable", "-", None),
        (False, "UNMEASURABLE", "unmeasurable", "-", None),
        (False, "not_scored", "unmeasurable", "-", None),
    ],
)
def test_the_label_comes_from_what_code_found_and_not_from_the_loops_verdict(
    accepted, score, stop, gate, label
):
    assert label_of(_checked(accepted, score, stop, gate)) == label


def test_a_failure_the_node_caused_on_an_accepted_round_is_left_out_not_blamed_on_the_plan():
    assert judge(_checked(True, "PASS", "FAIL")) == (
        None,
        "accepted, only node behaviour failed",
    )
    assert judge(_checked(False, "PASS", "FAIL")) == (
        "fail",
        "rejected and code agrees",
    )


def test_text_is_cut_to_one_line_with_an_ellipsis():
    assert clip("a  b\n c", 10) == "a b c"
    assert clip("x" * 20, 5) == "xxxx…"


def _hypothesis(rnd: int = 1, **attempt: Any) -> dict[str, Any]:
    one: dict[str, Any] = {
        "round": rnd,
        "plan": {
            "hypothesis": "Swap codons near the start.",
            "expected": "Some variants score higher.",
            "assertions": [
                {
                    "criterion": "up",
                    "step": "keep",
                    "branch": "produced",
                    "claim": "Kept variants beat the first input.",
                }
            ],
            "steps": {
                "mutate": {
                    "node": "mutate_synonymous__06de6f3e",
                    "inputs": {"sequence": "seqs"},
                    "why": "Make variants.",
                }
            },
        },
        "dag": {
            "steps": {
                "mutate": {
                    "config": {
                        "name": "mutate_synonymous",
                        "config_hash": "06de6f3e",
                        "seed": 1,
                        "reference": {"kind": "dna", "sequence": SEQ},
                    }
                }
            }
        },
        "held": {"keep.produced": False},
        "verdict": {
            "achieved": False,
            "agrees": False,
            "covers_goal": True,
            "reason": "The kept variants tie the baseline.",
        },
        "critique": {
            "diagnosis": "The threshold sits far below the baseline.",
            "root_cause": "wrong_config",
            "evidence": ["threshold=1000 against a baseline of 63163"],
            "fix": "Read the baseline and set the threshold above it.",
        },
        **attempt,
    }
    return {
        "goal": "Raise the rate.",
        "inputs": {"seqs": [{"kind": "dna", "sequence": SEQ}] * 3},
        "criteria": [{"id": "up", "claim": "Kept variants beat the first input."}],
        "round": 3,
        "attempts": [one],
    }


def test_a_round_reads_as_goal_plan_run_verifier_critique_and_code(tmp_path: Path):
    text = render(_hypothesis(), _hypothesis()["attempts"][0], _checked(False))
    assert text.startswith("Recorded round 26acc0f5:r1:")
    assert "Inputs: seqs: 3 dna of 15-15 characters" in text
    assert SEQ not in text  # What was given is described, never copied.
    assert "step mutate: mutate_synonymous " in text and "06de6f3e" not in text
    assert '{"seed":1}' in text  # Bulky entities in a config are left out.
    assert "This was round 1 of 3, and the loop rejected it." in text
    assert "keep.produced did not hold" in text
    assert "agrees with the builder's claims=False" in text
    assert "root cause wrong_config" in text and "fix it proposed" in text
    assert text.rstrip().endswith("in expression")


def test_a_round_that_never_ran_says_so_and_a_missing_part_leaves_its_line_out():
    data = _hypothesis(held={}, verdict=None, critique=None)
    text = render(data, data["attempts"][0], _checked())
    assert "nothing ran" in text
    assert "verifier" not in text and "critique" not in text
    data = _hypothesis(
        held={},
        error="the node raised",
        requests=[{"name": "codon_gc", "purpose": "gc"}],
    )
    text = render(data, data["attempts"][0], _checked())
    assert "error: the node raised" in text
    assert "asked for a new node codon_gc" in text and "nothing ran" not in text


def _archive(
    tmp_path: Path, rows: list[Checked], *, save: bool = True
) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    (runs / "pr7" / "arm" / "hypotheses").mkdir(parents=True)
    (runs / "index.json").write_text(
        json.dumps({"runs": [{"hypothesis_id": HID, "folder": "pr7/arm"}]})
    )
    if save:
        saved = _hypothesis(1)
        saved["attempts"] += [_hypothesis(2)["attempts"][0]]
        (runs / "pr7" / "arm" / "hypotheses" / f"{HID}.json").write_text(
            json.dumps(saved)
        )
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([r.model_dump() for r in rows]))
    return runs, labels


def test_rounds_are_read_labelled_and_the_ones_left_out_are_counted_by_reason(
    tmp_path: Path,
):
    runs, labels = _archive(
        tmp_path,
        [
            _checked(True, "PASS", "ok", "PASS", rnd=1),
            _checked(False, "FAIL", "FAIL", rnd=2),
            _checked(True, "PASS", "FAIL", rnd=3),
            _checked(False, "FAIL", rnd=4),
        ],
    )
    rounds = read_rounds(runs, labels)
    assert [(r.id, r.label) for r in rounds.records] == [
        ("26acc0f5:r1", "pass"),
        ("26acc0f5:r2", "fail"),
    ]
    assert rounds.reasons == {
        "pass: accepted and code agrees": 1,
        "fail: score failed": 2,
        "left out: accepted, only node behaviour failed": 1,
    }
    assert rounds.missing == [
        f"{HID}:r4"
    ]  # Labelled, but the file holds no such round.


def test_a_run_whose_file_is_gone_is_reported_not_silently_skipped(tmp_path: Path):
    runs, labels = _archive(tmp_path, [_checked()], save=False)
    rounds = read_rounds(runs, labels)
    assert rounds.records == [] and rounds.missing == [HID]


def test_a_hypothesis_that_no_longer_loads_as_a_model_is_still_read(tmp_path: Path):
    runs, labels = _archive(tmp_path, [_checked()])
    path = runs / "pr7" / "arm" / "hypotheses" / f"{HID}.json"
    saved = json.loads(path.read_text())
    saved["inputs"] = {"seq": {"kind": "dna"}}  # The older shape: a bare entity.
    saved["a_field_nothing_knows"] = 1
    path.write_text(json.dumps(saved))
    (record,) = read_rounds(runs, labels).records
    assert "seq: 1 dna" in record.text


def test_a_dry_run_counts_the_rounds_and_calls_no_model(tmp_path: Path):
    runs, labels = _archive(tmp_path, [_checked(), _checked(False, "FAIL", rnd=2)])
    out = tmp_path / "SKILL.md"
    done = CliRunner().invoke(
        main,
        ["--runs", str(runs), "--labels", str(labels), "--out", str(out), "--dry-run"],
    )
    assert done.exit_code == 0, done.output
    assert "rounds to read: {'pass': 1, 'fail': 1}" in done.output
    assert "tokens of rounds" in done.output
    assert not out.exists()


def test_a_run_writes_the_skill_where_it_was_asked(tmp_path: Path):
    runs, labels = _archive(tmp_path, [_checked()])
    out = tmp_path / "skills" / "hypothesis-builder" / "SKILL.md"
    done = CliRunner().invoke(
        main,
        [
            "--runs",
            str(runs),
            "--labels",
            str(labels),
            "--out",
            str(out),
            "--model",
            "test",
        ],
    )
    assert done.exit_code == 0, done.output
    assert out.read_text().startswith(SEED.split("\n")[0])
    assert "edits applied" in done.output


def test_what_the_stack_added_after_pr12_is_a_file_the_repo_keeps():
    assert KNOWN_SINCE.is_file()
    text = KNOWN_SINCE.read_text()
    assert "result_source" in text and "trim_to_first_start" in text


def test_the_analysts_are_told_the_builders_instructions_and_what_came_after_pr12(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runs, labels = _archive(tmp_path, [_checked()])
    extra = tmp_path / "extra.md"
    extra.write_text("An extra thing the builder knows.")
    seen: list[str] = []

    async def fake(skill: str, records: Any, model: Any, **kw: Any) -> Evolution:
        seen.append(kw["known"])
        return Evolution(
            skill=skill,
            applied=[],
            rejected=[],
            proposed=0,
            silent=0,
            levels=0,
            merge_failures=0,
            dropped=0,
            tokens=0,
        )

    monkeypatch.setattr(skills_cli, "evolve", fake)
    args = ["--runs", str(runs), "--labels", str(labels), "--out"]
    done = CliRunner().invoke(main, [*args, str(tmp_path / "a.md")])
    assert done.exit_code == 0, done.output
    done = CliRunner().invoke(
        main, [*args, str(tmp_path / "b.md"), "--known", str(extra)]
    )
    assert done.exit_code == 0, done.output
    default, only_extra = seen
    assert default.startswith(build_instructions(False))
    assert KNOWN_SINCE.read_text() in default
    assert "An extra thing" not in default
    assert only_extra.endswith("An extra thing the builder knows.")
    assert "result_source" not in only_extra  # Naming a file replaces the default.


# --- how the builder worked a plan -----------------------------------------------------


def _at(second: int) -> datetime:
    return datetime(2026, 10, 4, 9, 0, second, tzinfo=UTC)


def _worked(finished: bool = True) -> list[ModelMessage]:
    """A plan call that listed nodes, described two, made one with a config sent as a string,
    was sent back once by a guard, and then answered, unless ``finished`` is False."""

    def called(*calls: tuple[str, Any]) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(n, a, tool_call_id=f"{n}{i}")
                for i, (n, a) in enumerate(calls)
            ],
            model_name="m",
            timestamp=_at(1),
        )

    def returned(name: str) -> ModelRequest:
        return ModelRequest(
            parts=[
                ToolReturnPart(
                    name,
                    "a node list of twenty thousand characters",
                    tool_call_id=f"{name}0",
                )
            ]
        )

    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("go", timestamp=_at(0))]),
        called(("list_nodes", {})),
        returned("list_nodes"),
        called(
            ("describe_node", {"name": "gc_content"}),
            ("describe_node", {"name": "top_k"}),
            (
                "create_node",
                {
                    "config": '{"name": "ostir_expression", "seed": 1}',
                    "description": "d",
                },
            ),
            ("create_node", {"config": '{"name": "cut_off", ', "description": "d"}),
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    "No assertion covers ['up'].", tool_name=None, tool_call_id="f"
                )
            ]
        ),
    ]
    if finished:
        messages += [
            called(("final_result", {})),
            ModelRequest(
                parts=[
                    ToolReturnPart("final_result", "ok", tool_call_id="final_result0")
                ]
            ),
        ]
    return messages


def test_a_trail_names_what_was_read_and_made_and_what_a_guard_sent_back():
    lines = trail(_worked())
    assert lines[0] == "How the builder worked:"
    assert "  looked up: list_nodes x1" in lines
    assert "  described nodes: gc_content, top_k" in lines
    # A config sent as a string is named by its "name", even when the string is cut short.
    assert "  made nodes: ostir_expression, cut_off" in lines
    assert any("sent its answer back 1 times" in line for line in lines)
    assert "    No assertion covers ['up']." in lines
    assert not any("twenty thousand" in line for line in lines)
    assert not any("never gave a plan" in line for line in lines)


def test_a_plan_call_that_never_passed_the_guards_says_so():
    assert any("never gave a plan" in line for line in trail(_worked(finished=False)))


def test_a_plan_given_at_once_has_no_trail():
    answered = ModelResponse(
        parts=[ToolCallPart("final_result", {}, tool_call_id="f")], timestamp=_at(1)
    )
    done = ModelRequest(
        parts=[ToolReturnPart("final_result", "ok", tool_call_id="f", timestamp=_at(2))]
    )
    first = ModelRequest(parts=[UserPromptPart("go", timestamp=_at(0))])
    assert trail([first, answered, done]) == []


def _trajectory(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "trajectory.jsonl.gz"
    with gzip.open(path, "wt") as f:
        f.writelines(json.dumps(row) + "\n" for row in rows)
    return path


def test_trails_are_read_by_round_from_the_plan_calls_only(tmp_path: Path):
    data = to_jsonable_python(_worked(), serialize_unknown=True)
    assert ModelMessagesTypeAdapter.validate_python(
        data
    )  # The rows are what a run writes.
    path = _trajectory(
        tmp_path,
        [
            {"file": f"{HID}-r2-plan.json", "data": data},
            {"file": f"{HID}-r2-verify.json", "data": data},
            {"file": f"{HID}-r3-plan.json", "data": [{"kind": "an older shape"}]},
            {"file": "notes.txt", "data": data},
        ],
    )
    trails = read_trails(path)
    assert list(trails) == [2]
    assert "  described nodes: gc_content, top_k" in trails[2]


def test_a_trajectory_file_that_cannot_be_read_has_no_trails(tmp_path: Path):
    assert read_trails(tmp_path / "missing.jsonl.gz") == {}
    broken = tmp_path / "broken.jsonl.gz"
    broken.write_text("not gzip")
    assert read_trails(broken) == {}
