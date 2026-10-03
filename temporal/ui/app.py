import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowFailureError,
    WorkflowQueryFailedError,
)
from temporalio.service import RPCError

from node_dag.agent import Hypothesis
from node_dag.dag import DagProgress
from node_dag.plan import Criterion, HypothesisState, ToolRequest
from node_dag.registry import Registry
from node_dag.types import Value
from temporal.dag.activities import SaveWorkflowInput, results_subdir
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.loop import HypothesisInput, HypothesisLoop

logger = logging.getLogger(__name__)

INDEX = Path(__file__).with_name("index.html")
NEW = Path(__file__).with_name("new.html")
HYPOTHESES = Path(__file__).with_name("hypotheses.html")
NODES = Path(__file__).with_name("nodes.html")


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
    status: HypothesisState
    progress: DagProgress | None
    updated: datetime


def _hypothesis_row(path: Path) -> HypothesisRow:
    hyp = Hypothesis.model_validate_json(path.read_bytes())
    att = hyp.current
    progress = None
    if (
        att
        and att.workflow_id
        and (saved := SaveWorkflowInput.path_for(att.workflow_id)).exists()
    ):
        progress = DagProgress.model_validate_json(saved.read_bytes())
    status: HypothesisState
    if hyp.state:
        status = hyp.state
    elif att and att.verdict:
        status = "achieved" if att.verdict.achieved else "not achieved"
    elif att and att.outcome:
        status = "verifying"
    elif progress and "failed" in progress.steps.values():
        status = "failed"
    elif att and att.dag:
        status = "running"
    else:
        status = "building"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return HypothesisRow(
        hypothesis=hyp, status=status, progress=progress, updated=mtime
    )


class Goal(BaseModel):
    """A goal that has hypotheses.

    Args:
        goal: The goal text.
        hypotheses: How many hypotheses it has.
        inputs: The inputs of its most recently saved hypothesis.
        updated: When its most recent hypothesis was saved.
    """

    goal: str
    hypotheses: int
    inputs: dict[str, list[Value]]
    updated: datetime


def _saved_rows() -> list[HypothesisRow]:
    """Every saved Hypothesis that still validates, newest first.

    A file written before a schema change no longer loads. Skip it, so one stale
    file does not take out the whole page.
    """
    rows = []
    for path in results_subdir("hypotheses").glob("*.json"):
        try:
            rows.append(_hypothesis_row(path))
        except ValidationError as e:
            logger.warning("Skipping unreadable hypothesis %s: %s", path.name, e)
    return sorted(rows, key=lambda r: r.updated, reverse=True)


def _goals(rows: list[HypothesisRow]) -> list[Goal]:
    goals: dict[str, Goal] = {}
    for r in sorted(rows, key=lambda r: r.updated, reverse=True):
        h = r.hypothesis
        if h.goal in goals:
            goals[h.goal].hypotheses += 1
        else:
            goals[h.goal] = Goal(
                goal=h.goal, hypotheses=1, inputs=h.inputs, updated=r.updated
            )
    return list(goals.values())


def _run(ex: WorkflowExecution) -> Run:
    return Run(
        id=ex.id,
        status=ex.status.name if ex.status else "UNKNOWN",
        start_time=ex.start_time,
        close_time=ex.close_time,
    )


class NewHypothesis(BaseModel):
    """What the user gives to start a hypothesis.

    Args:
        goal: What the DAG must do, in plain English.
        hypothesis: The user's idea of how to meet the goal. The builder agent takes
            it as a starting point.
        inputs: The values to run on, keyed by DAG input name.
        criteria: What must be true for the goal to be met. Derived from the goal if empty.
        max_rounds: Most plans to try. Omit for the default.
    """

    goal: str = Field(min_length=1)
    hypothesis: str | None = None
    inputs: dict[str, list[Value]]
    criteria: list[str] = []
    max_rounds: int | None = Field(default=None, ge=1, le=10)


class RequestRow(BaseModel):
    """A node someone asked for, and the hypotheses blocked on it.

    Args:
        request: The contract, as the builder wrote it.
        blocked: Ids of the hypotheses waiting for this node.
    """

    request: ToolRequest
    blocked: list[str]


def make_app(
    client: Client,
    build_model: str | None = None,
    verify_model: str | None = None,
) -> FastAPI:
    """Return the app: the pages and the JSON API under ``/api``.

    Args:
        client: The Temporal client.
        build_model: The builder agent's model. Without it, hypotheses cannot be started.
        verify_model: The verifier agent's model. Default: ``build_model``.
    """
    app = FastAPI(title="node-dag")

    @app.get("/new", include_in_schema=False)
    async def new_page() -> FileResponse:
        return FileResponse(NEW)

    @app.post("/api/hypotheses", status_code=202)
    async def start_hypothesis(new: NewHypothesis) -> Hypothesis:
        """Start the loop on a goal and return the Hypothesis. It runs in the background."""
        if build_model is None:
            raise HTTPException(503, "The server was started without --model.")
        criteria = [
            Criterion(id=f"c{n}", claim=c.strip())
            for n, c in enumerate(new.criteria, 1)
            if c.strip()
        ]
        hyp = Hypothesis(
            goal=new.goal.strip(),
            inputs=new.inputs,
            criteria=criteria,
            hypothesis=(new.hypothesis or "").strip() or None,
        )
        cfg = {"max_rounds": new.max_rounds} if new.max_rounds else {}
        inp = HypothesisInput(
            hypothesis=hyp,
            build_model=build_model,
            verify_model=verify_model or build_model,
            **cfg,
        )
        await client.start_workflow(
            HypothesisLoop.run, inp, id=hyp.id, task_queue=TASK_QUEUE
        )
        return hyp

    @app.post("/api/hypotheses/{hyp_id}/{signal}", status_code=202)
    async def signal_hypothesis(
        hyp_id: str, signal: Literal["tool_added", "abandon"]
    ) -> None:
        """Tell a blocked run its node was added (resolve the plan again), or give up on it."""
        try:
            await client.get_workflow_handle(hyp_id).signal(signal)
        except RPCError as e:
            raise HTTPException(404, f"No running hypothesis {hyp_id}: {e}") from e

    @app.get("/api/requests")
    async def requests() -> list[RequestRow]:
        """Every requested node, the one most runs are blocked on first."""
        waiting: dict[str, list[str]] = {}
        for r in _saved_rows():
            for q in r.hypothesis.pending if r.status == "blocked" else []:
                waiting.setdefault(q.name, []).append(r.hypothesis.id)
        rows = [
            RequestRow(
                request=ToolRequest.model_validate_json(p.read_bytes()),
                blocked=waiting.get(p.stem, []),
            )
            for p in results_subdir("requests").glob("*.json")
        ]
        return sorted(rows, key=lambda r: len(r.blocked), reverse=True)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(INDEX)

    @app.get("/hypotheses", include_in_schema=False)
    async def hypotheses_page() -> FileResponse:
        return FileResponse(HYPOTHESES)

    @app.get("/nodes", include_in_schema=False)
    async def nodes_page() -> FileResponse:
        return FileResponse(NODES)

    @app.get("/api/nodes")
    async def nodes() -> list[dict[str, Any]]:
        """Every registered node as the builder agent sees it, in id order."""
        return [n.summary() for n in Registry(results_subdir("registry")).all()]

    @app.get("/api/hypotheses")
    async def hypotheses() -> list[HypothesisRow]:
        """Every saved Hypothesis, the most recently saved first."""
        return _saved_rows()

    @app.get("/api/goals")
    async def goals() -> list[Goal]:
        """Every goal with a saved Hypothesis, the most recently used first."""
        return _goals(_saved_rows())

    @app.get("/api/runs")
    async def runs(limit: int = 50) -> list[Run]:
        """The newest DagWorkflow runs first."""
        query = f"WorkflowType = '{DagWorkflow.__name__}'"
        return [_run(ex) async for ex in client.list_workflows(query, limit=limit)]

    @app.get("/api/runs/{workflow_id}")
    async def run(workflow_id: str) -> RunDetail:
        """One run, with the status of each step."""
        handle = client.get_workflow_handle(workflow_id)
        run = _run(await handle.describe())
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
