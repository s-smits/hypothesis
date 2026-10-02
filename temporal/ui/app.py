from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowFailureError,
    WorkflowQueryFailedError,
)
from temporalio.service import RPCError

from node_dag.agent import Hypothesis
from node_dag.dag import DagProgress
from temporal.dag.activities import SaveWorkflowInput
from temporal.dag.workflow import DagWorkflow
from temporal.run_hypothesis import hypotheses_dir

INDEX = Path(__file__).with_name("index.html")
HYPOTHESES = Path(__file__).with_name("hypotheses.html")

HypothesisStatus = Literal[
    "building", "running", "failed", "verifying", "achieved", "not achieved"
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


def _hypothesis_row(path: Path) -> HypothesisRow:
    hyp = Hypothesis.model_validate_json(path.read_bytes())
    progress = None
    if (
        hyp.workflow_id
        and (saved := SaveWorkflowInput.path_for(hyp.workflow_id)).exists()
    ):
        progress = DagProgress.model_validate_json(saved.read_bytes())
    status: HypothesisStatus
    if hyp.verdict:
        status = "achieved" if hyp.verdict.achieved else "not achieved"
    elif hyp.outcome:
        status = "verifying"
    elif progress and "failed" in progress.steps.values():
        status = "failed"
    elif hyp.dag:
        status = "running"
    else:
        status = "building"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return HypothesisRow(
        hypothesis=hyp, status=status, progress=progress, updated=mtime
    )


def _run(ex: WorkflowExecution) -> Run:
    return Run(
        id=ex.id,
        status=ex.status.name if ex.status else "UNKNOWN",
        start_time=ex.start_time,
        close_time=ex.close_time,
    )


def make_app(client: Client) -> FastAPI:
    """Return the app: the page at ``/`` and the JSON API under ``/api``."""
    app = FastAPI(title="node-dag")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(INDEX)

    @app.get("/hypotheses", include_in_schema=False)
    async def hypotheses_page() -> FileResponse:
        return FileResponse(HYPOTHESES)

    @app.get("/api/hypotheses")
    async def hypotheses() -> list[HypothesisRow]:
        """Every saved Hypothesis, the most recently saved first."""
        rows = [_hypothesis_row(p) for p in hypotheses_dir().glob("*.json")]
        return sorted(rows, key=lambda r: r.updated, reverse=True)

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
