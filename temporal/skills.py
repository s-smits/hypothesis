"""``python -m temporal.skills``: distil the rounds of recorded runs into the builder's SKILL.md.

Each round the loop recorded is one trajectory: the goal, the criteria, the plan the builder
wrote, what the plan did when it ran, the verifier's reading and, for a round that was not
accepted, the critique. ``node_dag.skills`` reads them in groups and edits one SKILL.md; this
module is the edge that finds the rounds, labels them and renders each as the text an analyst
reads.

The label is never the loop's own verdict. ``--labels`` is a JSON list of the checks that code
made on each round afterwards (``score_run.py``): the score against the first input, whether
the stop codon survived, and the benchmark gate. ``label_of`` turns those into pass, fail or
neither, and a round that is neither teaches nothing. A failure of the stop codon or the gate
on a round the loop accepted is left out, because the node's behaviour caused it and the
builder could not have planned around it.

Reading is tolerant: an archived Hypothesis that no longer loads as a model is still a JSON
file, so a round is read as plain data and a missing part leaves its line out. Nothing here
calls a model unless ``--dry-run`` is absent.

The analysts are also given what the builder already reads, its own instructions, and a list of
what the stack changed after PR #12, so that the skill is a delta over the #12 builder and says
nothing the later changes made true or fixed.
"""

import asyncio
import gzip
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import click
import httpx2
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ToolCallPart,
)
from pydantic_ai.settings import ModelSettings

from node_dag.agent import build_instructions
from node_dag.skills import SEED, Label, Record, evolve
from temporal.hypothesis.activities import CONNECT_TIMEOUT, REQUEST_TIMEOUT
from temporal.pulse import OUTPUT, TRANSCRIPT, read_call

# What the stack added after PR #12, which the analysts are told never to teach.
KNOWN_SINCE = Path(__file__).resolve().parent.parent / (
    "skills/hypothesis-builder/known-since-pr12.md"
)
# Characters of free text an analyst reads per field; a plan is long, a rule is short.
CLIP = 700
# A model's tokens are about this many characters, for the size estimate of a dry run.
CHARS_PER_TOKEN = 3.5
Reason = Literal[
    "score failed",
    "score empty",
    "rejected and code agrees",
    "accepted, only node behaviour failed",
    "accepted and code agrees",
    "rejected, code finds nothing wrong",
    "not measurable",
]


class Checked(BaseModel):
    """What code found about one round after the loop was done with it."""

    hypothesis_id: str
    round: int
    accepted_by_loop: bool
    score_check: str
    stop_check: str
    gate: str = "-"
    evidence: str = ""


def judge(row: Checked) -> tuple[Label | None, Reason]:
    """The label for a round and why, from code's checks alone."""
    if row.score_check in ("FAIL", "EMPTY"):
        return "fail", "score failed" if row.score_check == "FAIL" else "score empty"
    if row.score_check != "PASS":
        return None, "not measurable"
    defect = "FAIL" in (row.stop_check, row.gate)
    if defect and not row.accepted_by_loop:
        return "fail", "rejected and code agrees"
    if defect:
        return None, "accepted, only node behaviour failed"
    if row.stop_check not in ("ok", "n/a"):
        return None, "not measurable"
    if row.accepted_by_loop:
        return "pass", "accepted and code agrees"
    return "pass", "rejected, code finds nothing wrong"


def label_of(row: Checked) -> Label | None:
    """Pass, fail, or None for a round that should teach nothing."""
    return judge(row)[0]


def clip(text: object, n: int = CLIP) -> str:
    """``text`` on one line, cut to ``n`` characters."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


def _name(node: str) -> str:
    """A node's name without the hash that its configuration adds."""
    return node.split("__")[0]


def _inputs(data: dict[str, Any]) -> str:
    """What the goal was given, by kind and count, never the sequences themselves."""
    parts = []
    for name, value in (data.get("inputs") or {}).items():
        items = value if isinstance(value, list) else [value]
        kinds = sorted({str(i.get("kind", "?")) for i in items if isinstance(i, dict)})
        sizes = [len(i.get("sequence", "")) for i in items if isinstance(i, dict)]
        size = f" of {min(sizes)}-{max(sizes)} characters" if any(sizes) else ""
        parts.append(f"{name}: {len(items)} {'/'.join(kinds) or 'value'}{size}")
    return "; ".join(parts) or "none"


def _config(step: dict[str, Any]) -> str:
    """A step's configuration, as the builder set it, without bulky entities."""
    config = {
        k: v
        for k, v in (step.get("config") or {}).items()
        if k not in ("name", "config_hash") and not isinstance(v, dict)
    }
    return clip(json.dumps(config, separators=(",", ":")), 160) if config else "{}"


def _plan(attempt: dict[str, Any]) -> list[str]:
    plan = attempt.get("plan") or {}
    ran = (attempt.get("dag") or {}).get("steps") or {}
    out = ["Plan:", f"  hypothesis: {clip(plan.get('hypothesis', ''))}"]
    out.append(f"  expected: {clip(plan.get('expected', ''))}")
    for sid, step in (plan.get("steps") or {}).items():
        wired = ", ".join(f"{p}<-{s}" for p, s in (step.get("inputs") or {}).items())
        config = _config(ran.get(sid) or {})
        out.append(
            f"  step {sid}: {_name(str(step.get('node', '?')))} {config} ({wired})"
            f" — {clip(step.get('why', ''), 200)}"
        )
    for a in plan.get("assertions") or []:
        out.append(
            f"  assertion for {a.get('criterion')} at {a.get('step')}.{a.get('branch')}:"
            f" {clip(a.get('claim', ''), 220)}"
        )
    return out


def _outcome(attempt: dict[str, Any]) -> list[str]:
    out = ["What happened when it ran:"]
    if attempt.get("error"):
        out.append(f"  error: {clip(attempt['error'])}")
    for r in attempt.get("requests") or []:
        out.append(
            f"  the builder asked for a new node {r.get('name')}: "
            f"{clip(r.get('purpose', ''), 240)}"
        )
    held = attempt.get("held") or {}
    if held:
        out.append(
            "  assertions: "
            + ", ".join(
                f"{k} {'held' if v else 'did not hold'}" for k, v in held.items()
            )
        )
    elif not attempt.get("error"):
        out.append("  nothing ran")
    return out


def _verdict(attempt: dict[str, Any]) -> list[str]:
    v = attempt.get("verdict")
    if not v:
        return []
    return [
        (
            f"The verifier (a different model): achieved={v.get('achieved')}, "
            f"agrees with the builder's claims={v.get('agrees')}, "
            f"assertions cover the goal={v.get('covers_goal')}."
        ),
        f"  its reason: {clip(v.get('reason', ''))}",
    ]


def _critique(attempt: dict[str, Any]) -> list[str]:
    c = attempt.get("critique")
    if not c:
        return []
    out = [f"The builder's own critique afterwards, root cause {c.get('root_cause')}:"]
    out.append(f"  diagnosis: {clip(c.get('diagnosis', ''))}")
    for item in (c.get("evidence") or [])[:5]:
        out.append(f"  evidence: {clip(item, 220)}")
    out.append(f"  fix it proposed: {clip(c.get('fix', ''))}")
    return out


def _calls(messages: list[ModelMessage], tool: str, field: str) -> list[str]:
    """What the builder named in each call of ``tool``, e.g. the nodes it described."""
    named = []
    for m in messages:
        for p in m.parts:
            if isinstance(p, ToolCallPart) and p.tool_name == tool:
                try:
                    value = p.args_as_dict().get(field)
                except ValueError:
                    continue
                if isinstance(value, str):  # A config sent as a JSON string.
                    found = re.search(r'"name"\s*:\s*"([^"]+)"', value)
                    value = {"name": found[1]} if found else value
                name = value.get("name") if isinstance(value, dict) else value
                named.append(clip(name, 60))
    return named


def trail(messages: list[ModelMessage]) -> list[str]:
    """How the builder worked one plan: what it read and made, and what the guards sent back.

    The tool answers are left out. A node list is twenty thousand characters, and what the
    builder did with it shows in the nodes it went on to describe and the plan it wrote.
    """
    call = read_call(0, "plan", messages, saved=0.0)
    out = ["How the builder worked:"]
    described = _calls(messages, "describe_node", "name")
    made = _calls(messages, "create_node", "config")
    other = {
        k: n for k, n in call.tools.items() if k not in ("describe_node", "create_node")
    }
    if other:
        out.append("  looked up: " + ", ".join(f"{k} x{n}" for k, n in other.items()))
    if described:
        out.append(f"  described nodes: {', '.join(described)}")
    if made:
        out.append(f"  made nodes: {', '.join(made)}")
    if call.retries:
        out.append(f"  a guard sent its answer back {len(call.retries)} times:")
        out += [f"    {clip(why, 200)}" for why in call.retries[:4]]
    if not call.done:
        out.append(f"  it never gave a plan that passed the {OUTPUT} guards")
    return out if len(out) > 1 else []


def read_trails(path: Path) -> dict[int, list[str]]:
    """The trail of every plan call in a run's trajectory file, by round. Empty if unreadable."""
    trails: dict[int, list[str]] = {}
    try:
        with gzip.open(path, "rt") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return trails
    for row in rows:
        match = TRANSCRIPT.search(str(row.get("file", "")))
        if match is None or match[2] != "plan":
            continue
        try:
            messages = ModelMessagesTypeAdapter.validate_python(row["data"])
        except (KeyError, ValidationError):
            continue  # A transcript of an older shape leaves its trail out.
        trails[int(match[1])] = trail(messages)
    return trails


def render(
    hypothesis: dict[str, Any],
    attempt: dict[str, Any],
    row: Checked,
    worked: list[str] | None = None,
) -> str:
    """One round as the text an analyst reads, and how the builder worked if that is known."""
    n = attempt.get("round")
    rounds = hypothesis.get("round") or n
    loop = "accepted" if row.accepted_by_loop else "rejected"
    lines = [
        f"Recorded round {row.hypothesis_id[11:19]}:r{n}:",
        f"Goal: {clip(hypothesis.get('goal', ''))}",
        f"Inputs: {_inputs(hypothesis)}",
        "Success criteria:",
    ]
    lines += [
        f"  {c.get('id')}: {clip(c.get('claim', ''), 240)}"
        for c in hypothesis.get("criteria") or []
    ]
    lines += [f"This was round {n} of {rounds}, and the loop {loop} it.", ""]
    for part in (
        _plan(attempt),
        worked or [],
        _outcome(attempt),
        _verdict(attempt),
        _critique(attempt),
    ):
        lines += [*part, ""] if part else []
    lines.append(f"Checked afterwards by code: {clip(row.evidence, 500)}")
    return "\n".join(lines)


class Rounds(BaseModel):
    """What was read, and why each round that was left out was left out."""

    records: list[Record]
    reasons: dict[str, int]
    missing: list[str] = []


def read_rounds(runs: Path, labels: Path) -> Rounds:
    """Every labelled round under the archive ``runs``, rendered, with the reasons for the rest."""
    index = json.loads((runs / "index.json").read_text())["runs"]
    folders = {r["hypothesis_id"]: r["folder"] for r in index}
    records: list[Record] = []
    reasons: Counter[str] = Counter()
    missing: list[str] = []
    trails: dict[str, dict[int, list[str]]] = {}
    for item in json.loads(labels.read_text()):
        row = Checked.model_validate(item)
        label, reason = judge(row)
        reasons[f"{label or 'left out'}: {reason}"] += 1
        if label is None:
            continue
        path = runs / folders.get(row.hypothesis_id, "") / "hypotheses"
        path = path / f"{row.hypothesis_id}.json"
        if not path.is_file():
            missing.append(row.hypothesis_id)
            continue
        hypothesis = json.loads(path.read_text())
        attempt = next(
            (
                a
                for a in hypothesis.get("attempts") or []
                if a.get("round") == row.round
            ),
            None,
        )
        if attempt is None:
            missing.append(f"{row.hypothesis_id}:r{row.round}")
            continue
        if row.hypothesis_id not in trails:
            folder = runs / folders[row.hypothesis_id] / "trajectories"
            trails[row.hypothesis_id] = read_trails(
                folder / f"{row.hypothesis_id}.jsonl.gz"
            )
        text = render(
            hypothesis, attempt, row, trails[row.hypothesis_id].get(row.round)
        )
        records.append(
            Record(
                id=f"{row.hypothesis_id[11:19]}:r{row.round}", label=label, text=text
            )
        )
    return Rounds(records=records, reasons=dict(reasons), missing=missing)


@click.command()
@click.option(
    "--runs", type=click.Path(exists=True, path_type=Path), default="docs/runs"
)
@click.option("--labels", type=click.Path(exists=True, path_type=Path), required=True)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default="skills/hypothesis-builder/SKILL.md",
)
@click.option("--model", default="anthropic:claude-sonnet-5-5", show_default=True)
@click.option(
    "--known",
    "known_files",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    default=(KNOWN_SINCE,),
    show_default=True,
    help="Text the analysts must not repeat or rely on, besides the builder's own "
    "instructions. Repeat it to add files; the default is what changed after PR #12.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Count the rounds and the size of the work; call no model.",
)
def main(
    runs: Path,
    labels: Path,
    out: Path,
    model: str,
    known_files: tuple[Path, ...],
    dry_run: bool,
) -> None:
    """Distil the labelled rounds under RUNS into SKILL.md at OUT."""
    rounds = read_rounds(runs, labels)
    by_label = Counter(r.label for r in rounds.records)
    for why, n in sorted(rounds.reasons.items()):
        click.echo(f"{n:4d}  {why}")
    click.echo(
        f"rounds to read: {dict(by_label)}; missing files: {len(rounds.missing)}"
    )
    chars = sum(len(r.text) for r in rounds.records)
    click.echo(f"about {round(chars / CHARS_PER_TOKEN):,} tokens of rounds, once each")
    if dry_run or not rounds.records:
        return
    settings = ModelSettings(
        timeout=httpx2.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
    )
    start = out.read_text() if out.is_file() else SEED
    known = "\n\n".join(
        [build_instructions(False), *(f.read_text() for f in known_files)]
    )
    done = asyncio.run(
        evolve(start, rounds.records, model, settings=settings, known=known)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(done.skill)
    click.echo(
        f"{len(done.applied)} edits applied, {len(done.rejected)} rejected, "
        f"{done.silent} analysts silent, {done.tokens:,} tokens → {out}"
    )


if __name__ == "__main__":
    main()
