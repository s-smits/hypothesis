import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai.models import Model
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowFailureError,
    WorkflowQueryFailedError,
)
from temporalio.service import RPCError, RPCStatusCode

from node_dag.dag import DagProgress
from node_dag.factory import MAPPING
from node_dag.plan import Criterion, Hypothesis, ToolRequest
from node_dag.types import TYPES, Value
from temporal.dag.activities import SaveWorkflowInput
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.models import HypothesisInput
from temporal.hypothesis.workflow import HypothesisWorkflow
from temporal.store import hypotheses_dir, requests_dir, save_hypothesis

logger = logging.getLogger(__name__)
_WARNED_INCOMPATIBLE: set[tuple[Path, int]] = set()

INDEX = Path(__file__).with_name("index.html")
NEW = Path(__file__).with_name("new.html")
HYPOTHESES = Path(__file__).with_name("hypotheses.html")
REQUESTS = Path(__file__).with_name("requests.html")

# Every HypothesisState the workflow can record, plus the statuses the waterfall below
# derives for files written before states existed. ``cls()`` in hypotheses.html turns one
# of these into a CSS class, so each needs an ``.s-*`` rule there.
HypothesisStatus = Literal[
    "building",
    "running",
    "failed",
    "verifying",
    "critiquing",
    "blocked",
    "achieved",
    "not achieved",
    "unverified",
    "abandoned",
]


class Run(BaseModel):
    """One DagWorkflow execution.

    Args:
        id: The workflow ID.
        status: The Temporal status, e.g. ``RUNNING``, ``COMPLETED`` or ``FAILED``.
        start_time: When it started.
        close_time: When it finished, if it has.
    """

    id: str
    status: str
    start_time: datetime
    close_time: datetime | None


class RunDetail(Run):
    """A run and how far it has got.

    Args:
        progress: The run as save_workflow wrote it, once it has finished; else the
            answer to its ``progress`` query. None if neither is available, e.g. no
            worker is running to answer the query.
        error: Why the run failed, or why the query failed.
    """

    progress: DagProgress | None
    error: str | None


class HypothesisRow(BaseModel):
    """A saved Hypothesis and its run.

    Args:
        hypothesis: The Hypothesis as run_hypothesis last saved it.
        status: How far it has got, worked out from what is saved. A run whose
            process died stays at the stage it died in.
        progress: The run as save_workflow wrote it, once the run has finished.
        updated: When the Hypothesis was last saved.
    """

    hypothesis: Hypothesis
    status: HypothesisStatus
    progress: DagProgress | None
    updated: datetime


def _waterfall(hyp: Hypothesis, progress: DagProgress | None) -> HypothesisStatus:
    """How far a Hypothesis got, worked out from the fields a single pass filled in.

    The rule from before ``Hypothesis.state`` existed, unchanged: every results file on
    disk predates the workflow, so ``state=None`` must keep resolving through here.

    Args:
        hyp: The saved Hypothesis.
        progress: Its run as save_workflow wrote it, if it has one.
    """
    if hyp.verdict:
        return "achieved" if hyp.verdict.achieved else "not achieved"
    if hyp.outcome:
        return "verifying"
    if progress and "failed" in progress.steps.values():
        return "failed"
    if hyp.dag:
        return "running"
    return "building"


def _hypothesis_row(path: Path) -> HypothesisRow:
    hyp = Hypothesis.model_validate_json(path.read_bytes())
    progress = None
    if (
        hyp.workflow_id
        and (saved := SaveWorkflowInput.path_for(hyp.workflow_id)).exists()
    ):
        progress = DagProgress.model_validate_json(saved.read_bytes())
    # The workflow records where it is; nothing else can tell "blocked" from "running".
    status: HypothesisStatus = hyp.state or _waterfall(hyp, progress)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return HypothesisRow(
        hypothesis=hyp, status=status, progress=progress, updated=mtime
    )


def _saved_rows() -> list[HypothesisRow]:
    """Every compatible saved Hypothesis, newest first.

    Results from a different schema are ignored so one stale file cannot break the page.
    """
    rows = []
    for path in hypotheses_dir().glob("*.json"):
        try:
            rows.append(_hypothesis_row(path))
        except ValidationError as e:
            key = (path, path.stat().st_mtime_ns)
            if key not in _WARNED_INCOMPATIBLE:
                logger.warning("Skipping incompatible hypothesis %s: %s", path.name, e)
                _WARNED_INCOMPATIBLE.add(key)
    return sorted(rows, key=lambda row: row.updated, reverse=True)


def _run(ex: WorkflowExecution) -> Run:
    return Run(
        id=ex.id,
        status=ex.status.name if ex.status else "UNKNOWN",
        start_time=ex.start_time,
        close_time=ex.close_time,
    )


class Kind(BaseModel):
    """One Value kind a DAG input can carry.

    Read from ``node_dag.types.TYPES`` so the new-hypothesis form builds its dropdown
    from the registry instead of a hand-maintained mirror that goes stale.

    Args:
        kind: The discriminator value, e.g. ``dna``.
        field: The field the value carries it in, e.g. ``sequence``.
        type: That field's Python type name, so a form knows whether to parse a number.
    """

    kind: str
    field: str
    type: str


def _kinds() -> list[Kind]:
    """Every Value kind, with the field its value carries, named alphabetically."""
    kinds = []
    for name, model in TYPES.items():
        field = next(f for f in model.model_fields if f != "kind")
        annotation = model.model_fields[field].annotation
        kinds.append(
            Kind(
                kind=name,
                field=field,
                type=getattr(annotation, "__name__", str(annotation)),
            )
        )
    return sorted(kinds, key=lambda k: k.kind)


class BlockedHypothesis(BaseModel):
    """A hypothesis waiting on a requested tool.

    Args:
        id: Its Hypothesis id, so the page can link to the detail view.
        goal: What it is trying to do.
    """

    id: str
    goal: str


class ToolRequestRow(BaseModel):
    """One requested node and how much work is stuck behind it.

    Args:
        request: The contract, as the builder submitted it.
        blocked: Every hypothesis currently blocked on this name.
        goals: How many distinct goals those hypotheses belong to.
        satisfied: Whether this process can already build the node. A worker that has
            not been restarted since the node was written still reports False.
    """

    request: ToolRequest
    blocked: list[BlockedHypothesis]
    goals: int
    satisfied: bool


def _node_names() -> set[str]:
    """The node names this process can build, from ``factory.MAPPING``."""
    return {c.model_fields["name"].default for c in MAPPING}


def _load[T: BaseModel](model: type[T], path: Path) -> T | None:
    """``path`` parsed as ``model``, or None with a warning when it will not parse."""
    try:
        return model.model_validate_json(path.read_bytes())
    except (ValidationError, ValueError) as e:
        logger.warning("Ignoring %s: %s", path, e)
        return None


def _request_rows() -> list[ToolRequestRow]:
    """Every requested tool, the one blocking the most hypotheses first.

    **The blocked list is computed, never stored.** ``results/requests/<name>.json``
    holds only the contract, because two hypotheses blocking on the same tool would race
    a read-modify-write of a shared list inside it. So who is waiting comes from scanning
    the hypotheses for ``state == "blocked"`` and a matching ``pending`` name.
    """
    requests: dict[str, ToolRequest] = {}
    for path in sorted(requests_dir().glob("*.json")):
        if req := _load(ToolRequest, path):
            requests[req.name] = req
    blocked: dict[str, list[BlockedHypothesis]] = {}
    for path in sorted(hypotheses_dir().glob("*.json")):
        hyp = _load(Hypothesis, path)
        if hyp is None or hyp.state != "blocked":
            continue
        for req in hyp.pending:
            requests.setdefault(req.name, req)
            blocked.setdefault(req.name, []).append(
                BlockedHypothesis(id=hyp.id, goal=hyp.goal)
            )
    have = _node_names()
    rows = [
        ToolRequestRow(
            request=req,
            blocked=blocked.get(name, []),
            goals=len({b.goal for b in blocked.get(name, [])}),
            satisfied=name in have,
        )
        for name, req in requests.items()
    ]
    return sorted(rows, key=lambda r: (-len(r.blocked), -r.goals, r.request.name))


class RootCauseStat(BaseModel):
    """How often one root cause was diagnosed, split by whether the tools existed.

    Args:
        root_cause: The ``Critique.root_cause`` value.
        total: How many critiques named it.
        tools_existed: How many of those rounds needed no node that was missing.
        tools_missing: How many asked for a node that did not exist.
    """

    root_cause: str
    total: int
    tools_existed: int
    tools_missing: int


class Stats(BaseModel):
    """What the loop is actually failing on: a tool gap or a reasoning gap.

    Failures clustering on a round that was missing a node mean the answer is more
    tools. Failures diagnosed as ``goal_misread`` or ``wrong_node`` *while every node it
    needed existed* mean reasoning is the bottleneck — and only that second case
    justifies a deeper agent.

    Args:
        hypotheses: How many Hypothesis files were read.
        attempts: How many rounds they ran between them.
        critiques: How many of those rounds were diagnosed.
        tool_gap: Critiques from a round that asked for a node that did not exist.
        reasoning_gap: Critiques from a round where every node it needed existed.
        blocked: How many hypotheses are waiting on a human right now.
        requests: How many distinct tools have been requested.
        unsatisfied: How many of those this process still cannot build.
        by_root_cause: The tally, the most common first.
        usage: Tokens spent across every hypothesis, keyed by stage.
    """

    hypotheses: int
    attempts: int
    critiques: int
    tool_gap: int
    reasoning_gap: int
    blocked: int
    requests: int
    unsatisfied: int
    by_root_cause: list[RootCauseStat]
    usage: dict[str, int]


def _stats() -> Stats:
    """Tally ``Critique.root_cause`` against whether the needed tools existed."""
    counts: dict[str, list[int]] = {}  # root_cause -> [existed, missing]
    hypotheses = attempts = blocked = 0
    usage: dict[str, int] = {}
    for path in sorted(hypotheses_dir().glob("*.json")):
        hyp = _load(Hypothesis, path)
        if hyp is None:
            continue
        hypotheses += 1
        blocked += hyp.state == "blocked"
        for stage, tokens in hyp.usage.items():
            usage[stage] = usage.get(stage, 0) + tokens
        for att in hyp.attempts:
            attempts += 1
            if att.critique is None:
                continue
            wanted = att.requests or (list(att.plan.requests) if att.plan else [])
            tally = counts.setdefault(att.critique.root_cause, [0, 0])
            tally[1 if wanted else 0] += 1
    rows = [
        RootCauseStat(
            root_cause=cause,
            total=existed + missing,
            tools_existed=existed,
            tools_missing=missing,
        )
        for cause, (existed, missing) in counts.items()
    ]
    rows.sort(key=lambda r: (-r.total, r.root_cause))
    requests = _request_rows()
    return Stats(
        hypotheses=hypotheses,
        attempts=attempts,
        critiques=sum(r.total for r in rows),
        tool_gap=sum(r.tools_missing for r in rows),
        reasoning_gap=sum(r.tools_existed for r in rows),
        blocked=blocked,
        requests=len(requests),
        unsatisfied=sum(1 for r in requests if not r.satisfied),
        by_root_cause=rows,
        usage=usage,
    )


class Note(BaseModel):
    """An optional line recorded with a signal.

    Args:
        note: Why the person resumed or abandoned the run.
    """

    note: str | None = None


class Ack(BaseModel):
    """Confirmation that a signal reached a run.

    Args:
        id: The hypothesis signalled.
        signal: Which signal was sent.
    """

    id: str
    signal: str


class NewHypothesis(BaseModel):
    """What the user gives to start a hypothesis.

    Args:
        goal: What the DAG must do, in plain English.
        hypothesis: The user's idea of how to meet the goal. The builder agent takes
            it as a starting point.
        inputs: The values to run on, keyed by DAG input name.
        criteria: What must be true for the goal to be met. Frozen before any plan is
            written; left empty, the criteria agent derives them.
        max_rounds: Plan-run-verify rounds before giving up.
    """

    goal: str = Field(min_length=1)
    hypothesis: str | None = None
    inputs: dict[str, Value]
    criteria: list[Criterion] = []
    max_rounds: int = Field(default=3, ge=1, le=10)


def make_app(
    client: Client,
    build_model: Model | str | None = None,
    verify_model: Model | str | None = None,
    critique_model: Model | str | None = None,
) -> FastAPI:
    """Return the app: the pages and the JSON API under ``/api``.

    Args:
        client: The Temporal client.
        build_model: The builder agent's model. Without it, hypotheses cannot be started.
        verify_model: The verifier agent's model. Default: ``build_model``.
        critique_model: The critic's model. Default: ``build_model``.
    """
    app = FastAPI(title="node-dag")

    @app.get("/new", include_in_schema=False)
    async def new_page() -> FileResponse:
        return FileResponse(NEW)

    @app.post("/api/hypotheses", status_code=202)
    async def start_hypothesis(new: NewHypothesis) -> Hypothesis:
        """Start a hypothesis as a durable workflow and return it at once."""
        if build_model is None:
            raise HTTPException(503, "The server was started without --model.")
        hyp = Hypothesis(
            goal=new.goal.strip(),
            hypothesis=(new.hypothesis or "").strip() or None,
            inputs=new.inputs,
            criteria=new.criteria,
            state="building",
        )
        logger.info("Starting hypothesis %s: %s", hyp.id, hyp.goal)
        # Saved before the workflow starts, so the page has something to show even when
        # no worker is up to pick the run off the queue.
        save_hypothesis(hyp)
        await client.start_workflow(
            HypothesisWorkflow.run,
            HypothesisInput(
                hypothesis=hyp,
                proposed=hyp.hypothesis,
                build_model=str(build_model),
                verify_model=str(verify_model or build_model),
                critique_model=str(critique_model or build_model),
                max_rounds=new.max_rounds,
            ),
            id=hyp.id,
            task_queue=TASK_QUEUE,
        )
        return hyp

    async def _signal(hypothesis_id: str, name: str, note: str | None) -> Ack:
        """Send one signal to a running hypothesis, turning Temporal errors into HTTP."""
        try:
            handle = client.get_workflow_handle(hypothesis_id)
            await handle.signal(name, note)
        except RPCError as e:
            # Temporal answers both "never existed" and "already finished" with
            # NOT_FOUND on a signal, so say the softer thing and never 500.
            if e.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(
                    404, f"No running hypothesis {hypothesis_id}."
                ) from e
            raise HTTPException(409, f"Could not signal {hypothesis_id}: {e}") from e
        return Ack(id=hypothesis_id, signal=name)

    @app.post("/api/hypotheses/{hypothesis_id}/tool-added", status_code=202)
    async def tool_added(hypothesis_id: str, note: Note | None = None) -> Ack:
        """Tell a blocked run to re-resolve its plan against this worker's nodes.

        Re-resolving is a fresh activity, so it sees a node registered since the run
        blocked. If the worker was not restarted it will find the same nodes and block
        again, with a note saying so.
        """
        return await _signal(hypothesis_id, "tool_added", note.note if note else None)

    @app.post("/api/hypotheses/{hypothesis_id}/abandon", status_code=202)
    async def abandon(hypothesis_id: str, note: Note | None = None) -> Ack:
        """Stop a run at its next checkpoint."""
        return await _signal(hypothesis_id, "abandon", note.note if note else None)

    @app.post("/api/requests/{name}/tool-added", status_code=202)
    async def resume_all(name: str) -> list[Ack]:
        """Resume every run blocked on ``name``. One click after writing one node."""
        acks = []
        for row in _request_rows():
            if row.request.name != name:
                continue
            for blocked in row.blocked:
                try:
                    acks.append(await _signal(blocked.id, "tool_added", None))
                except HTTPException:
                    logger.warning("Could not resume %s", blocked.id)
        return acks

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(INDEX)

    @app.get("/hypotheses", include_in_schema=False)
    async def hypotheses_page() -> FileResponse:
        return FileResponse(HYPOTHESES)

    @app.get("/requests", include_in_schema=False)
    async def requests_page() -> FileResponse:
        return FileResponse(REQUESTS)

    @app.get("/api/kinds")
    async def kinds() -> list[Kind]:
        """Every Value kind a DAG input can carry, read from the type registry."""
        return _kinds()

    @app.get("/api/requests")
    async def tool_requests() -> list[ToolRequestRow]:
        """The tool backlog, the request blocking the most hypotheses first."""
        return _request_rows()

    @app.get("/api/stats")
    async def stats() -> Stats:
        """Whether the loop is short of tools or short of reasoning."""
        return _stats()

    @app.get("/api/hypotheses")
    async def hypotheses() -> list[HypothesisRow]:
        """Every compatible saved Hypothesis, the most recently saved first."""
        return _saved_rows()

    @app.get("/api/runs")
    async def runs(limit: int = 50) -> list[Run]:
        """The newest DagWorkflow runs first."""
        query = f"WorkflowType = '{DagWorkflow.__name__}'"
        return [_run(ex) async for ex in client.list_workflows(query, limit=limit)]

    @app.get("/api/runs/{workflow_id}")
    async def run(workflow_id: str) -> RunDetail:
        """One run, with the status of each step."""
        handle = client.get_workflow_handle(workflow_id)
        try:
            run = _run(await handle.describe())
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, f"No run {workflow_id}.") from e
            raise HTTPException(503, f"Could not reach Temporal: {e}") from e
        progress, error = None, None
        # A finished run is on disk, so it needs no worker, and an old run need not
        # replay under the current workflow code.
        saved = SaveWorkflowInput.path_for(workflow_id)
        if run.close_time and saved.exists():
            progress = DagProgress.model_validate_json(saved.read_bytes())
        else:
            try:
                progress = await handle.query(
                    DagWorkflow.progress, rpc_timeout=timedelta(seconds=5)
                )
            except (RPCError, WorkflowQueryFailedError) as e:
                error = f"Could not query progress: {e}"
        if run.status == "FAILED":
            try:
                await handle.result()
            except WorkflowFailureError as e:
                cause: BaseException = e
                while cause.__cause__:  # The activity error wraps the node's error.
                    cause = cause.__cause__
                error = str(cause)
        return RunDetail(**run.model_dump(), progress=progress, error=error)

    return app
