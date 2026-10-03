"""The ledger: one line per finished run, in the terms pulse reads a run in.

Pulse reads a run from its saved files. A ledger line is that reading of a run that has
ended, kept in ``$NODE_DAG_RESULTS/ledger.jsonl``, so every value can be checked against the
files it came from. Nothing here calls a model.
"""

import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from temporal.dag.activities import results_root, results_subdir
from temporal.pulse import Reading, held_text, read_run


class Entry(BaseModel):
    """One run's line in the ledger.

    Args:
        run: The hypothesis id and when the run began, so writing a line twice adds one.
        state: How the run ended.
        why: Why it ended, in the loop's own words.
        rounds: Plans tried.
        held: Per round, the assertions that held out of those asked (``3/4``), ``error`` for
            a round that failed, or ``-``.
        tokens: Tokens the models used, over every stage.
        seconds: From the run's first sign to its last save.
        retries: Times a guard sent a model's answer back.
        errors: Rounds that ended in an error.
        repeats: Rounds whose wiring an earlier round had already run.
        nodes: Every node the plans used, requested ones included.
        asked: Every node a plan asked for that did not exist when it was planned.
        models: The model that answered each stage.
        summary: The run in one line.
    """

    run: str
    hypothesis: str
    goal: str
    ended: datetime
    state: str
    why: str | None = None
    rounds: int
    held: list[str]
    tokens: int
    seconds: float
    retries: int
    errors: int
    repeats: int
    nodes: list[str]
    asked: list[str]
    models: dict[str, str] = {}
    summary: str


def ledger_path() -> Path:
    """``$NODE_DAG_RESULTS/ledger.jsonl``: one line per finished run."""
    return results_root() / "ledger.jsonl"


def entry_for(r: Reading) -> Entry:
    """The ledger line for a run, from pulse's reading of it."""
    wirings = [x.wiring for x in r.rounds if x.wiring]
    asked = sorted({n for x in r.rounds for n in x.asked})
    summary = f"{r.state} in {len(r.rounds)} round{'' if len(r.rounds) == 1 else 's'}"
    if r.stopped:
        summary += f": {r.stopped}"
    if asked:
        summary += f"; asked for {', '.join(asked)}"
    return Entry(
        run=f"{r.id}@{round(r.started * 1000)}",
        hypothesis=r.id,
        goal=r.goal,
        ended=datetime.fromtimestamp(r.saved, UTC),
        state=r.state,
        why=r.stopped,
        rounds=len(r.rounds),
        held=[held_text(x) or "-" for x in r.rounds],
        tokens=r.tokens,
        seconds=round(r.saved - r.started, 1),
        retries=sum(len(c.retries) for c in r.calls),
        errors=sum(bool(x.error) for x in r.rounds),
        repeats=len(wirings) - len(set(wirings)),
        nodes=sorted({n for x in r.rounds for n in x.nodes}),
        asked=asked,
        models={c.stage: c.model for c in r.calls if c.model},
        summary=summary,
    )


def append(path: Path, entry: Entry) -> bool:
    """Add a line, unless this run is already in the ledger. Returns whether it was added."""
    if any(e.run == entry.run for e in read(path)[0]):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # One write in append mode: another run's line cannot land in the middle of this one.
    with path.open("a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")
    return True


def read(path: Path) -> tuple[list[Entry], int]:
    """Every entry in the ledger, oldest first, and how many lines could not be read."""
    if not path.exists():
        return [], 0
    entries, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(Entry.model_validate_json(line))
        except ValidationError:
            bad += 1
    return entries, bad


@activity.defn
def record_ledger(hyp_id: str) -> None:
    """Add a finished run to the ledger, reading its saved files as pulse does."""
    r = read_run(results_subdir("hypotheses") / f"{hyp_id}.json", time.time(), 0)
    if isinstance(r, str):  # Why it cannot be read: retrying will not change it.
        raise ApplicationError(f"{hyp_id} cannot be read: {r}", non_retryable=True)
    append(ledger_path(), entry_for(r))
