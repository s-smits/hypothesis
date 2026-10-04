"""``python -m temporal.pulse``: what changed in the open hypotheses since the last look.

A run that is being watched raises a different question every few minutes: not where it
stands, but what moved. This keeps one reading per run between looks and prints the
difference as events: ``◆`` a stage worth reading (criteria fixed, a round opened, a plan
accepted, blocked, the verdict), ``⚠`` something that may be wrong (a model call that needed
many retries, a budget nearly spent) and ``·`` a smaller fact. A first look, with no reading
kept, prints status lines only.

Read-only: every value comes from a file the loop recorded, and a file that cannot be read
leaves its part of the reading out. What a transcript shows is how much guard friction a plan
cost and which nodes the builder read, which the Hypothesis file does not keep.

A blocked run is the one that waits on a person, so its status line says what each requested
node still needs: scaffolding, a body for ``run``, a factory edit, or only a Resume.
"""

import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

import click
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

import node_dag.nodes
from node_dag.agent import Hypothesis
from node_dag.plan import Attempt
from node_dag.storage import write_atomic
from temporal.dag.activities import results_root, results_subdir
from temporal.scaffold_node import camel

# Waiting on a person is normal; waiting this long is worth a nudge.
BLOCKED_S = 30 * 60
# Retries in one model call that make a pattern of friction rather than ordinary noise.
FRICTION = 3
SPENT = 0.8
# The loop's default token budget, HypothesisInput.max_tokens, which is not imported because
# the loop module costs a third of a second to start; a test keeps the two equal.
BUDGET = 500_000
# pydantic-ai's name for an agent's output tool, which is not one of the builder's own.
OUTPUT = "final_result"
TERMINAL = frozenset({"achieved", "not achieved", "abandoned", "failed"})
WORKING = frozenset({"building", "running", "verifying", "critiquing"})
TRANSCRIPT = re.compile(r"-r(\d+)-(criteria|plan|verify|critique)\.json$")
UUID_ID = re.compile(r"^hypothesis-([0-9a-f]{8})(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")

Mark = Literal["◆", "⚠", "·"]
NodeState = Literal["missing", "scaffolded", "unregistered", "ready"]
NEXT: dict[NodeState, str] = {
    "missing": "scaffold it",
    "scaffolded": "write its run()",
    "unregistered": "add it to factory.py",
    "ready": "in place",
}


class Event(BaseModel):
    """One thing that moved, with the files to read next, relative to the results root."""

    mark: Mark
    label: str
    text: str
    look: list[str] = []


class Call(BaseModel):
    """One model call, read from its transcript."""

    round: int
    stage: str
    model: str | None = None
    tools: dict[str, int] = {}
    retries: list[str] = []
    tokens: int = 0
    began: float
    took: float
    done: bool
    saved: float


class Round(BaseModel):
    """One attempt, as the Hypothesis file records it."""

    number: int
    steps: int | None = None
    wiring: str | None = None
    nodes: list[str] = []
    asked: list[str] = []
    requests: list[str] = []
    held: dict[str, bool] = {}
    achieved: bool | None = None
    reason: str | None = None
    cause: str | None = None
    error: str | None = None


class Reading(BaseModel):
    """Everything pulse keeps of one run between looks."""

    id: str
    label: str
    goal: str
    state: str
    now: float
    saved: float
    started: float
    round: int
    criteria: list[str] = []
    rounds: list[Round] = []
    calls: list[Call] = []
    pending: dict[str, NodeState] = {}
    tokens: int = 0
    budget: int
    stopped: str | None = None


class Memory(BaseModel):
    """What one look leaves for the next."""

    readings: dict[str, Reading] = {}


def dur(seconds: float) -> str:
    """``seconds`` as ``45s``, ``2m10s`` or ``1h05m``."""
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m{s:02d}s" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def thousands(n: int) -> str:
    """``n`` as ``74k`` once it is large."""
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def label_of(hyp_id: str) -> str:
    """The first eight characters of a generated id, or a chosen id as it is."""
    match = UUID_ID.match(hyp_id)
    return match[1] if match else hyp_id


def _why(content: object) -> str:
    """One line saying why a retry was asked for: the first problem, where it was."""
    if isinstance(content, list) and content and isinstance(content[0], dict):
        first = content[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        return f"{where}: {first.get('msg', '')}".strip(": ")[:140]
    return str(content).strip().splitlines()[0][:140] if str(content).strip() else ""


def read_call(
    number: int, stage: str, messages: list[ModelMessage], saved: float
) -> Call:
    """A model call as its transcript shows it: tools read, retries sent, time and tokens."""
    tools: Counter[str] = Counter()
    retries: list[str] = []
    stamps: list[float] = []
    tokens, model = 0, None
    for m in messages:
        if isinstance(m, ModelResponse):
            tokens += m.usage.total_tokens
            model = m.model_name or model
            stamps.append(m.timestamp.timestamp())
            tools.update(
                p.tool_name
                for p in m.parts
                if isinstance(p, ToolCallPart) and p.tool_name != OUTPUT
            )
            continue
        for p in m.parts:
            if stamp := getattr(p, "timestamp", None):
                stamps.append(stamp.timestamp())
            if isinstance(p, RetryPromptPart):
                own = "" if p.tool_name in (None, OUTPUT) else f"{p.tool_name}: "
                retries.append(own + _why(p.content))
    last = messages[-1].parts[-1] if messages and messages[-1].parts else None
    # Done is the output tool's return, or, for the stages that answer in plain text
    # (criteria, critique), a last response that calls no tool.
    answered = (
        bool(messages)
        and isinstance(messages[-1], ModelResponse)
        and not any(isinstance(p, ToolCallPart) for p in messages[-1].parts)
    )
    done = answered or (isinstance(last, ToolReturnPart) and last.tool_name == OUTPUT)
    began = min(stamps, default=saved)
    return Call(
        round=number,
        stage=stage,
        model=model,
        tools=dict(tools),
        retries=retries,
        tokens=tokens,
        began=began,
        took=max(stamps, default=began) - began,
        done=done,
        saved=saved,
    )


def read_calls(hyp_id: str) -> list[Call]:
    """Every model call of one run that left a transcript, in round order."""
    calls = []
    for path in results_subdir("trajectories").glob(f"{hyp_id}-r*-*.json"):
        match = TRANSCRIPT.search(path.name)
        if match is None or path.name[: match.start()] != hyp_id:
            continue  # Another run whose id this one is the start of.
        try:
            messages = ModelMessagesTypeAdapter.validate_json(path.read_bytes())
            calls.append(
                read_call(int(match[1]), match[2], messages, path.stat().st_mtime)
            )
        except (OSError, ValueError):
            continue  # A transcript that cannot be read leaves its call out.
    return sorted(calls, key=lambda c: (c.round, c.began))


def node_state(name: str) -> NodeState:
    """How far a requested node has got on disk, and so what a person must still do."""
    root = Path(node_dag.nodes.__file__).parent
    found = [p for d in ("tools", "filters") if (p := root / d / name).is_dir()]
    if not found:
        return "missing"
    try:
        body = (found[0] / "function.py").read_text()
        factory = (root.parent / "factory.py").read_text()
    except OSError:
        return "scaffolded"
    if "NotImplementedError" in body:
        return "scaffolded"
    return "ready" if f"{camel(name)}Config" in factory else "unregistered"


def read_round(a: Attempt) -> Round:
    """One attempt in the terms pulse keeps."""
    plan, verdict, critique = a.plan, a.verdict, a.critique
    return Round(
        number=a.round,
        steps=len(plan.steps) if plan else None,
        wiring=plan.fingerprint()[:8] if plan else None,
        nodes=sorted({s.node for s in plan.steps.values()}) if plan else [],
        asked=sorted(plan.requests) if plan else [],
        requests=[r.name for r in a.requests],
        held=a.held,
        achieved=verdict.achieved if verdict else None,
        reason=verdict.reason if verdict else None,
        cause=critique.root_cause if critique else None,
        error=a.error,
    )


def _invalid(e: ValidationError) -> str:
    """The first sentence of why a file is not a Hypothesis, short enough for a header."""
    first = e.errors()[0]
    if first["type"] == "json_invalid":
        return "not JSON"
    why = first["msg"].removeprefix("Value error, ").split(". ")[0]
    return why if len(why) <= 90 else why[:89] + "…"


def read_run(
    path: Path,
    now: float,
    budget: int,
    wanted: Callable[[Reading], bool] = lambda _: True,
) -> Reading | str:
    """One run from its saved files, or why its Hypothesis cannot be read.

    A file written before a node's fields or version changed no longer validates, and is
    named with the reason rather than shown as empty. The transcripts, which are most of
    the work, are read only if ``wanted`` accepts the reading without them.
    """
    try:
        hyp = Hypothesis.model_validate_json(path.read_bytes())
        saved = path.stat().st_mtime
    except OSError as e:
        return f"cannot be read: {e.strerror or e}"
    except ValidationError as e:
        return _invalid(e)
    began = [a.started.timestamp() for a in hyp.attempts if a.started]
    verdict = hyp.current.verdict if hyp.current else None
    state = hyp.state or (
        "legacy"
        if verdict is None
        else "achieved"
        if verdict.achieved
        else "not achieved"
    )
    names = [r.name for r in hyp.pending]
    run = Reading(
        id=hyp.id,
        label=label_of(hyp.id),
        goal=hyp.goal,
        state=state,
        now=now,
        saved=saved,
        started=min(began, default=saved),
        round=hyp.round,
        criteria=[c.id for c in hyp.criteria],
        rounds=[read_round(a) for a in hyp.attempts],
        pending={n: node_state(n) for n in names},
        tokens=hyp.usage.get("total", 0),
        budget=budget,
        stopped=hyp.stopped_because,
    )
    if wanted(run):
        run.calls = read_calls(hyp.id)
        run.started = min([c.began for c in run.calls] + began, default=saved)
    return run


def waited(r: Reading) -> str | None:
    """A run that has waited on a person for a long time."""
    if r.state == "blocked" and r.now - r.saved >= BLOCKED_S:
        return f"blocked {dur(r.now - r.saved)} on {', '.join(r.pending) or '?'}"
    return None


def spent(r: Reading) -> str | None:
    """A run close to its token budget, which ends it."""
    if r.budget > 0 and r.state not in TERMINAL and r.tokens >= SPENT * r.budget:
        return f"{thousands(r.tokens)} of {thousands(r.budget)} tokens used"
    return None


# Each is said once when it starts and once when it clears.
ALERTS: dict[str, Callable[[Reading], str | None]] = {"waited": waited, "spent": spent}


def in_flight(r: Reading) -> str:
    """What the run is waiting on while it is in this state."""
    return {
        "building": "the criteria call" if not r.criteria else "the plan call",
        "running": "the DAG",
        "verifying": "the verify call",
        "critiquing": "the critique call",
    }.get(r.state, r.state)


def held_text(x: Round) -> str:
    """``3/4`` for a round whose assertions ran, ``error`` for one that failed, else empty."""
    if x.held:
        return f"{sum(x.held.values())}/{len(x.held)}"
    return "error" if x.error else ""


def events(before: Reading | None, after: Reading) -> list[Event]:
    """What moved between two readings of one run, in the order a reader wants it.

    Silent on the first reading, because there is nothing yet to differ from.
    """
    if before is None:
        return []
    out: list[Event] = []

    def say(mark: Mark, text: str, *look: str) -> None:
        out.append(Event(mark=mark, label=after.label, text=text, look=list(look)))

    hyp_file = f"hypotheses/{after.id}.json"
    if after.criteria and not before.criteria:
        say("◆", f"criteria fixed: {', '.join(after.criteria)}", hyp_file)
    was_rounds = {x.number: x for x in before.rounds}
    for i, x in enumerate(after.rounds):
        was = was_rounds.get(x.number)
        n = f"r{x.number}"
        if was is None:
            missed = after.rounds[i - 1] if i else None
            why = (
                f"; r{missed.number} missed ({missed.cause})"
                if missed and missed.cause
                else ""
            )
            say("◆", f"{n} opened{why}")
        if x.steps is not None and (was is None or was.steps is None):
            asked = f", asking for {', '.join(x.requests)}" if x.requests else ""
            say("◆", f"{n} plan accepted: {x.steps} steps{asked}", hyp_file)
        if x.held and not (was and was.held):
            failed = [k for k, ok in x.held.items() if not ok]
            tail = f"; did not hold: {', '.join(failed)}" if failed else ""
            say("◆", f"{n} assertions {held_text(x)}{tail}")
        if x.achieved is not None and (was is None or was.achieved is None):
            say(
                "◆",
                f"{n} {'achieved' if x.achieved else 'missed'}: {(x.reason or '')[:200]}",
            )
        if x.cause and not (was and was.cause):
            say("◆", f"{n} critique: {x.cause}")
        if x.error and not (was and was.error):
            say("⚠", f"{n} error: {x.error[:200]}")
    if after.state == "blocked" and before.state != "blocked":
        say(
            "◆",
            f"r{after.round} blocked on {', '.join(after.pending)}",
            *(f"requests/{n}.json" for n in after.pending),
        )
    if before.state == "blocked" and after.state not in ("blocked", *TERMINAL):
        say("◆", f"r{after.round} resumed")
    for name, now in after.pending.items():
        was_node = before.pending.get(name)
        if was_node is not None and was_node != now:
            say("·", f"node {name}: {was_node} → {now}")
    ready = all(s == "ready" for s in after.pending.values())
    if (
        after.pending
        and ready
        and not all(s == "ready" for s in before.pending.values())
    ):
        say(
            "◆",
            "every requested node is in place: restart the worker if it predates them, then Resume",
        )
    seen = {(c.round, c.stage): c for c in before.calls}
    for c in after.calls:
        was_call = seen.get((c.round, c.stage))
        if was_call is not None and was_call.saved == c.saved:
            continue
        noisy = len(c.retries) >= FRICTION or not c.done
        tools = ", ".join(f"{n} ×{k}" if k > 1 else n for n, k in c.tools.items())
        retried = f"{len(c.retries)} retr{'y' if len(c.retries) == 1 else 'ies'}"
        text = (
            f"r{c.round} {c.stage} call: {dur(c.took)}, {tools or 'no tools'}, "
            f"{retried}, {thousands(c.tokens)} tokens"
        )
        if not c.done:
            text += "; it did not finish"
        if noisy and c.retries:
            text += f"; sent back for: {'; '.join(c.retries[:3])}"
        say(
            "⚠" if noisy else "·",
            text,
            f"trajectories/{after.id}-r{c.round}-{c.stage}.json",
        )
    for name, alert in ALERTS.items():
        was_text, now_text = alert(before), alert(after)
        if now_text and not was_text:
            say("⚠", now_text)
        elif was_text and not now_text and after.state not in TERMINAL:
            say("·", f"{name} cleared")
    if after.state in TERMINAL and before.state not in TERMINAL:
        say(
            "◆",
            f"ended: {after.state}" + (f": {after.stopped}" if after.stopped else ""),
        )
    return out


def status(r: Reading, width: int) -> str:
    """One line saying where a run stands, in the terms of the state it is in."""
    idle = dur(r.now - r.saved)
    if r.state in TERMINAL:
        body = f"{r.state}: {r.stopped or '?'}"
    elif r.state == "blocked":
        body = f"r{r.round} blocked {idle} on {', '.join(r.pending) or '?'}"
    elif r.state in WORKING:
        body = f"r{r.round} {r.state} {idle}: {in_flight(r)}"
    else:
        body = r.state
    held = " ".join(f"r{x.number} {held_text(x)}" for x in r.rounds if held_text(x))
    retries = sum(len(c.retries) for c in r.calls)
    facts = [f"{thousands(r.tokens)}/{thousands(r.budget)} tokens"]
    if held:
        facts.insert(0, f"rounds {held}")
    if retries:
        facts.append(f"{retries} guard retr{'y' if retries == 1 else 'ies'}")
    tail = " | " + " · ".join(facts)
    return f"{r.label.ljust(width)} {dur(r.now - r.started).rjust(7)}  {body}{tail}"


def next_step(r: Reading) -> str | None:
    """What a person must do for a blocked run, one clause per requested node."""
    if r.state != "blocked" or not r.pending:
        return None
    if all(s == "ready" for s in r.pending.values()):
        return "every requested node is in place: restart the worker if it predates them, then Resume"
    parts = []
    for name, s in r.pending.items():
        hint = f"python -m temporal.scaffold_node {name}" if s == "missing" else NEXT[s]
        parts.append(f"{name} ({s}: {hint})")
    return "waiting on " + ", ".join(parts)


def selected(r: Reading, selectors: tuple[str, ...], watched: frozenset[str]) -> bool:
    """Whether this look reads the run. Selectors are the whole answer when given; with none,
    every open run is read, and so is one kept that has closed since, so its ending is said."""
    if selectors:
        return any(s in r.id or s in r.label or s in r.goal for s in selectors)
    return (r.state not in TERMINAL | {"legacy"}) or r.id in watched


def load_memory(path: Path) -> Memory:
    """A missing, unreadable or older file is a first look."""
    try:
        return Memory.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return Memory()


def save_memory(path: Path, memory: Memory) -> str | None:
    """Best effort, after the look was printed: a full disk costs the next look its events."""
    try:
        write_atomic(path, memory.model_dump_json().encode())
    except OSError as e:
        return f"pulse could not keep this look for the next one: {e}"
    return None


class Look(BaseModel):
    """One look: each run's status and what moved since the last look."""

    at: str
    runs: list[Reading]
    status: list[str]
    events: list[Event]
    unreadable: list[str]


def look(selectors: tuple[str, ...], memory: Memory, budget: int, now: float) -> Look:
    """Read the saved runs, compare each with its kept reading, and keep the new one."""
    watched = frozenset(memory.readings)

    def wanted(r: Reading) -> bool:
        return selected(r, selectors, watched)

    paths = sorted(results_subdir("hypotheses").glob("*.json"))
    readings = [(p, read_run(p, now, budget, wanted)) for p in paths]
    unreadable = [f"{p.name} ({r})" for p, r in readings if isinstance(r, str)]
    runs = [r for _, r in readings if isinstance(r, Reading) and wanted(r)]
    width = max((len(r.label) for r in runs), default=0)
    lines, moved = [], []
    for r in runs:
        lines.append(status(r, width))
        if r.id not in memory.readings:
            lines.append(f"{' ' * width} goal: {r.goal[:100]}")
        if hint := next_step(r):
            lines.append(f"{' ' * width} ↳ {hint}")
        moved += events(memory.readings.get(r.id), r)
        # A run that ended is said once and then no longer watched.
        if r.state in TERMINAL:
            memory.readings.pop(r.id, None)
        else:
            memory.readings[r.id] = r
    return Look(
        at=datetime.fromtimestamp(now).astimezone().strftime("%H:%M"),
        runs=runs,
        status=lines,
        events=moved,
        unreadable=unreadable,
    )


def render(seen: Look) -> list[str]:
    """The look as text: a header, a status line per run, then the events."""
    count = f"{len(seen.runs)} run{'' if len(seen.runs) == 1 else 's'}"
    if not seen.runs:
        count += f" under {results_root()}"
    bad = f" · unreadable: {', '.join(seen.unreadable)}" if seen.unreadable else ""
    lines = [f"{seen.at} {count}{bad}", *(f"  {line}" for line in seen.status)]
    for e in seen.events:
        where = f" → {', '.join(e.look)}" if e.look else ""
        lines.append(f"{seen.at} {e.mark} {e.label} {e.text}{where}")
    return lines


@click.command()
@click.argument("selectors", nargs=-1)
@click.option(
    "--every", type=float, help="Look again every N seconds until interrupted."
)
@click.option(
    "--state",
    "state_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where readings are kept between looks. Default: <results>/pulse.json.",
)
@click.option(
    "--budget",
    type=int,
    default=BUDGET,
    help="The runs' token budget, for the spent alert.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the look as JSON.")
def main(
    selectors: tuple[str, ...],
    every: float | None,
    state_path: Path | None,
    budget: int,
    as_json: bool,
) -> None:
    """Say what changed in the open hypotheses since the last look.

    With no SELECTORS, every open run is read. A selector is part of a run's id, label or
    goal. The first look prints status lines only; later looks add the events. Files are
    under $NODE_DAG_RESULTS (default results).
    """
    path = state_path or results_root() / "pulse.json"
    memory = load_memory(path)
    while True:
        seen = look(selectors, memory, budget, time.time())
        click.echo(
            seen.model_dump_json(indent=2) if as_json else "\n".join(render(seen))
        )
        if unsaved := save_memory(path, memory):
            click.echo(unsaved, err=True)
        if every is None:
            return
        try:
            time.sleep(every)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
