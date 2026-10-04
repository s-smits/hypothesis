import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowFailureError,
    WorkflowQueryFailedError,
)
from temporalio.service import RPCError, RPCStatusCode

from node_dag import amass
from node_dag.agent import Hypothesis, criteria_agent
from node_dag.dag import DagProgress
from node_dag.links import Link
from node_dag.plan import Criterion, HypothesisState, ToolRequest
from node_dag.registry import Registry
from node_dag.types import Value
from temporal.dag.activities import (
    SavedRun,
    SaveWorkflowInput,
    results_root,
    results_subdir,
    step_links,
)
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.hypothesis.activities import save_hypothesis
from temporal.hypothesis.loop import VERIFY_MODEL, HypothesisInput, HypothesisLoop

logger = logging.getLogger(__name__)

INDEX = Path(__file__).with_name("index.html")
NEW = Path(__file__).with_name("new.html")
HYPOTHESES = Path(__file__).with_name("hypotheses.html")
NODES = Path(__file__).with_name("nodes.html")
OBSERVATIONS = Path(__file__).with_name("observations.html")


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
        links: Somewhere to watch each step's work, keyed by step, for a step that
            runs its work elsewhere, e.g. on a GPU on Modal. A step reports its link
            as it starts, so the link is there while the step runs, and stays after,
            since that page keeps the logs.
    """

    progress: DagProgress | None
    error: str | None
    links: dict[str, list[Link]] = {}


class HypothesisRow(BaseModel):
    """A saved Hypothesis and its run.

    Args:
        hypothesis: The Hypothesis as HypothesisLoop last saved it.
        status: Its ``state``. For a file from before the loop, worked out from what
            is saved: the Hypothesis and its saved run.
        progress: The current round's run as save_workflow wrote it, once it has
            finished.
        updated: When the Hypothesis was last saved.
    """

    hypothesis: Hypothesis
    status: HypothesisState
    progress: SavedRun | None
    updated: datetime


def _hypothesis_row(path: Path) -> HypothesisRow:
    hyp = Hypothesis.model_validate_json(path.read_bytes())
    att = hyp.current
    saved, run_failed = None, False
    if att and att.workflow_id:
        run_path = SaveWorkflowInput.path_for(att.workflow_id)
        if saved := _saved_run(run_path):
            run_failed = _disk_run(run_path, saved).status != "COMPLETED"
    status: HypothesisState
    if hyp.state:
        status = hyp.state
    elif att and att.verdict:
        status = "achieved" if att.verdict.achieved else "not achieved"
    elif att and att.outcome:
        status = "verifying"
    elif run_failed:
        status = "failed"
    elif att and att.dag:
        status = "running"
    else:
        status = "building"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return HypothesisRow(hypothesis=hyp, status=status, progress=saved, updated=mtime)


class Goal(BaseModel):
    """A goal that has hypotheses.

    Args:
        goal: The goal text.
        hypotheses: How many hypotheses it has.
        inputs: The inputs of its most recently saved hypothesis.
        criteria: The success criteria of its most recently saved hypothesis.
        updated: When its most recent hypothesis was saved.
    """

    goal: str
    hypotheses: int
    inputs: dict[str, list[Value]]
    criteria: list[Criterion]
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
                goal=h.goal,
                hypotheses=1,
                inputs=h.inputs,
                criteria=h.criteria,
                updated=r.updated,
            )
    return list(goals.values())


def _run(ex: WorkflowExecution) -> Run:
    return Run(
        id=ex.id,
        status=ex.status.name if ex.status else "UNKNOWN",
        start_time=ex.start_time,
        close_time=ex.close_time,
    )


def _saved_run(path: Path) -> SavedRun | None:
    """The run save_workflow wrote to ``path``, or None if there is none."""
    if not path.exists():
        return None
    try:
        return SavedRun.model_validate_json(path.read_bytes())
    except ValidationError as e:
        logger.warning("Skipping unreadable run %s: %s", path.name, e)
        return None


def _disk_run(path: Path, saved: SavedRun) -> Run:
    # A file saved before SavedRun had run fields: work them out from its steps
    # and when it was written.
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    failed = "failed" in saved.steps.values()
    return Run(
        id=path.stem,
        status=saved.status or ("FAILED" if failed else "COMPLETED"),
        start_time=saved.start_time or mtime,
        close_time=saved.close_time or mtime,
    )


def _saved_runs() -> dict[str, Run]:
    """Every finished run on disk, keyed by workflow ID."""
    runs = {}
    for path in (results_root() / "workflows").glob("*.json"):
        if saved := _saved_run(path):
            runs[path.stem] = _disk_run(path, saved)
    return runs


class NewHypothesis(BaseModel):
    """What the user gives to start a hypothesis.

    Args:
        goal: What the DAG must do, in plain English.
        hypothesis: The user's idea of how to meet the goal. The builder agent takes
            it as a starting point.
        inputs: The values to run on, keyed by DAG input name. Omit them and the agent
            fetches the sequences the goal names from NCBI before round 1.
        criteria: What must be true for the goal to be met. Derived from the goal if empty.
        max_rounds: Most plans to try. Omit for the default.
    """

    goal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    hypothesis: str | None = None
    inputs: dict[str, list[Value]] = {}
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


class NewCriteria(BaseModel):
    """A goal to draft success criteria for.

    Args:
        goal: The goal to describe success for, in plain English.
        hypothesis: The user's idea of how to meet it, if they wrote one. It can
            sharpen what success means.
    """

    goal: str = Field(min_length=1)
    hypothesis: str | None = None


class Citation(BaseModel):
    """A hypothesis that cites an observation, and what it took from it.

    Args:
        hypothesis_id: The hypothesis.
        goal: Its goal.
        summary: What the builder agent took from the record for this hypothesis.
    """

    hypothesis_id: str
    goal: str
    summary: str


class ObservationDetail(BaseModel):
    """An Amass record and the hypotheses that cite it.

    Args:
        record: The record as Amass returns it, with its full text if it has one.
        cited_by: Every saved hypothesis that cites it.
    """

    record: dict[str, Any]
    cited_by: list[Citation]


def make_app(
    client: Client,
    build_model: str | None = None,
    verify_model: str | None = None,
) -> FastAPI:
    """Return the app: the pages and the JSON API under ``/api``.

    Args:
        client: The Temporal client.
        build_model: The builder agent's model. Without it, hypotheses cannot be started.
        verify_model: The verifier agent's model. Default: ``VERIFY_MODEL``.
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
        try:  # Before anything is saved: an empty or mixed input list is not a 500.
            hyp = Hypothesis(
                goal=new.goal.strip(),
                inputs=new.inputs,
                criteria=criteria,
                hypothesis=(new.hypothesis or "").strip() or None,
            )
        except ValidationError as e:
            raise HTTPException(
                422, "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
            ) from e
        cfg = {"max_rounds": new.max_rounds} if new.max_rounds else {}
        inp = HypothesisInput(
            hypothesis=hyp,
            build_model=build_model,
            verify_model=verify_model or VERIFY_MODEL,
            **cfg,
        )
        # Saved first, so the page has something to show before any worker picks it up.
        save_hypothesis(hyp.model_copy(update={"state": "building"}))
        try:
            await client.start_workflow(
                HypothesisLoop.run, inp, id=hyp.id, task_queue=TASK_QUEUE
            )
        except Exception as e:
            # No workflow will ever save this file again: end it, or it stays "building".
            why = f"the workflow could not be started: {e}"
            save_hypothesis(
                hyp.model_copy(update={"state": "failed", "stopped_because": why})
            )
            raise HTTPException(503, f"Could not start the run: {e}") from e
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
        rows = []
        for p in results_subdir("requests").glob("*.json"):
            try:  # One file that no longer validates must not take out the list.
                request = ToolRequest.model_validate_json(p.read_bytes())
            except ValidationError as e:
                logger.warning("Skipping unreadable request %s: %s", p.name, e)
                continue
            rows.append(RequestRow(request=request, blocked=waiting.get(p.stem, [])))
        return sorted(rows, key=lambda r: len(r.blocked), reverse=True)

    @app.post("/api/criteria")
    async def draft_criteria(new: NewCriteria) -> list[Criterion]:
        """Draft a goal's success criteria with the criteria agent, to edit next."""
        if build_model is None:
            raise HTTPException(503, "The server was started without --model.")
        prompt = f"Goal: {new.goal.strip()}"
        if new.hypothesis and new.hypothesis.strip():
            prompt += f"\nProposed hypothesis: {new.hypothesis.strip()}"
        try:
            result = await criteria_agent(build_model).run(prompt)
        except Exception as e:
            raise HTTPException(502, f"The criteria agent failed: {e}") from e
        return result.output

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

    @app.get("/observations", include_in_schema=False)
    async def observations_page() -> FileResponse:
        return FileResponse(OBSERVATIONS)

    @app.get("/api/observations/{core}/{amass_id}")
    async def observation(core: amass.Core, amass_id: str) -> ObservationDetail:
        """One Amass record with its full text, and every hypothesis that cites it."""
        try:
            # Fetched once, then read from results/amass.
            record = await asyncio.to_thread(
                amass.get_record, core, amass_id, amass.FULL_TEXT.get(core, ())
            )
        except amass.AmassError as e:
            raise HTTPException(502, str(e)) from e
        cited = [
            Citation(
                hypothesis_id=r.hypothesis.id, goal=r.hypothesis.goal, summary=o.summary
            )
            for r in _saved_rows()
            for o in r.hypothesis.observations
            if o.amass_id == amass_id
        ]
        return ObservationDetail(record=record, cited_by=cited)

    @app.get("/api/runs")
    async def runs(limit: int = 50) -> list[Run]:
        """The newest DagWorkflow runs first.

        Finished runs come from disk, so they outlive Temporal's history. Temporal
        adds the runs still going, if it is up.
        """
        runs = _saved_runs()
        query = f"WorkflowType = '{DagWorkflow.__name__}'"
        try:
            async for ex in client.list_workflows(query, limit=limit):
                runs[ex.id] = _run(ex)
        except RPCError as e:
            logger.warning("Could not list runs from Temporal: %s", e)
        newest = sorted(runs.values(), key=lambda r: r.start_time, reverse=True)
        return newest[:limit]

    @app.get("/api/runs/{workflow_id}")
    async def run(workflow_id: str) -> RunDetail:
        """One run, with the status of each step."""
        # A finished run is on disk, so it needs neither Temporal nor a worker, and
        # an old run need not replay under the current workflow code.
        path = SaveWorkflowInput.path_for(workflow_id)
        if saved := _saved_run(path):
            return RunDetail(
                **_disk_run(path, saved).model_dump(),
                progress=saved,
                error=saved.error,
                links=step_links(workflow_id),
            )
        handle = client.get_workflow_handle(workflow_id)
        try:
            run = _run(await handle.describe())
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, f"No run {workflow_id}") from e
            raise HTTPException(503, f"Could not reach Temporal: {e}") from e
        progress, error = None, None
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
        return RunDetail(
            **run.model_dump(),
            progress=progress,
            error=error,
            links=step_links(workflow_id),
        )

    return app
