import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

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

from node_dag import amass
from node_dag.agent import (
    Criterion,
    Hypothesis,
    Observation,
    criteria_agent,
    observations_agent,
)
from node_dag.dag import DagProgress
from node_dag.links import Link
from node_dag.registry import Registry
from node_dag.types import Value
from temporal.dag.activities import (
    SavedRun,
    SaveWorkflowInput,
    results_root,
    step_links,
)
from temporal.dag.workflow import DagWorkflow
from temporal.run_hypothesis import (
    hypotheses_dir,
    registry_dir,
    run_hypothesis,
    save_hypothesis,
)

logger = logging.getLogger(__name__)

INDEX = Path(__file__).with_name("index.html")
NEW = Path(__file__).with_name("new.html")
HYPOTHESES = Path(__file__).with_name("hypotheses.html")
NODES = Path(__file__).with_name("nodes.html")
OBSERVATIONS = Path(__file__).with_name("observations.html")

HypothesisStatus = Literal[
    "building",
    "running",
    "failed",
    "interrupted",
    "verifying",
    "achieved",
    "not achieved",
]
IN_PROGRESS: tuple[HypothesisStatus, ...] = ("building", "running", "verifying")


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
        hypothesis: The Hypothesis as run_hypothesis last saved it.
        status: How far it has got, worked out from what is saved: the Hypothesis
            and its saved run. One whose process died is ``interrupted``, once the
            UI has started since.
        progress: The run as save_workflow wrote it, once the run has finished.
        updated: When the Hypothesis was last saved.
    """

    hypothesis: Hypothesis
    status: HypothesisStatus
    progress: SavedRun | None
    updated: datetime


def _hypothesis_row(path: Path) -> HypothesisRow:
    hyp = Hypothesis.model_validate_json(path.read_bytes())
    saved, run_failed = None, False
    if hyp.workflow_id:
        run_path = SaveWorkflowInput.path_for(hyp.workflow_id)
        if saved := _saved_run(run_path):
            run_failed = _disk_run(run_path, saved).status != "COMPLETED"
    status: HypothesisStatus
    if hyp.verdict:
        status = "achieved" if hyp.verdict.achieved else "not achieved"
    elif hyp.interrupted:
        status = "interrupted"
    elif hyp.error or run_failed:
        status = "failed"
    elif hyp.outcome:
        status = "verifying"
    elif hyp.dag:
        status = "running"
    else:
        status = "building"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return HypothesisRow(hypothesis=hyp, status=status, progress=saved, updated=mtime)


def _mark_interrupted() -> None:
    """Save every Hypothesis still in progress as interrupted.

    Hypotheses run as tasks in the UI's process, so when the UI starts, none of
    them is running: one still in progress was left by a process that died. A
    process that is still running one elsewhere, e.g. ``run_hypothesis`` from the
    command line, puts it right at its next save. Keeps each file's time, so
    ``updated`` stays when it last got anywhere.
    """
    for row in _saved_rows():
        if row.status not in IN_PROGRESS:
            continue
        hyp = row.hypothesis
        logger.warning("Marking hypothesis %s interrupted (%s)", hyp.id, row.status)
        error = (
            f"Interrupted while {row.status}: the process running it stopped "
            "before it finished."
        )
        save_hypothesis(hyp.model_copy(update={"interrupted": True, "error": error}))
        t = row.updated.timestamp()
        os.utime(hypotheses_dir() / f"{hyp.id}.json", (t, t))


class Goal(BaseModel):
    """A goal that has hypotheses.

    Args:
        goal: The goal text.
        hypotheses: How many hypotheses it has.
        inputs: The inputs of its most recently saved hypothesis.
        criteria: The success criteria of its most recently saved hypothesis.
        observations: The observations of its most recently saved hypothesis, so a
            new run on the same goal starts from the literature already gathered
            rather than paying for the same search again.
        updated: When its most recent hypothesis was saved.
    """

    goal: str
    hypotheses: int
    inputs: dict[str, list[Value]]
    criteria: list[Criterion]
    observations: list[Observation] = []
    updated: datetime


def _saved_rows() -> list[HypothesisRow]:
    """Every saved Hypothesis that still validates, newest first.

    A file written before a schema change no longer loads. Skip it, so one stale
    file does not take out the whole page.
    """
    rows = []
    for path in hypotheses_dir().glob("*.json"):
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
                observations=h.observations,
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
        criteria: The success criteria of the goal, written by the user or
            drafted by the criteria agent and then edited. The builder sees them
            and the verifier judges against them.
        observations: What the literature says about the goal: gathered by the
            observations agent and then edited, so the builder is shown them as it
            chooses its nodes. Empty means it searches for itself, or not at all.
        inputs: The values to run on, keyed by DAG input name. Optional, and the
            page does not ask for them: left out, the builder agent works out what
            the goal is about and fetches the sequences itself.
    """

    goal: str = Field(min_length=1)
    hypothesis: str | None = None
    criteria: list[Criterion] = []
    observations: list[Observation] = []
    inputs: dict[str, list[Value]] = {}


class NewDraft(BaseModel):
    """A goal for an agent to draft something about, before a run starts.

    What ``/api/criteria`` and ``/api/observations`` both take: the goal, and the
    user's own idea of how to meet it where they wrote one.

    Args:
        goal: The goal, in plain English.
        hypothesis: The user's idea of how to meet it, if they wrote one. It can
            sharpen what success means, and what to search the literature for.
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
    build_model: Model | str | None = None,
    verify_model: Model | str | None = None,
) -> FastAPI:
    """Return the app: the pages and the JSON API under ``/api``.

    Marks every Hypothesis still in progress as interrupted, since none of them can
    be running in this process yet.

    Args:
        client: The Temporal client.
        build_model: The builder agent's model. Without it, hypotheses cannot be started.
        verify_model: The verifier agent's model. Default: ``build_model``.
    """
    _mark_interrupted()
    app = FastAPI(title="node-dag")
    tasks: set[asyncio.Task[Hypothesis]] = set()  # Keeps the running tasks alive.

    @app.get("/new", include_in_schema=False)
    async def new_page() -> FileResponse:
        return FileResponse(NEW)

    @app.post("/api/hypotheses", status_code=202)
    async def start_hypothesis(new: NewHypothesis) -> Hypothesis:
        """Start the agents on a goal and return the Hypothesis. They run in the background."""
        if build_model is None:
            raise HTTPException(503, "The server was started without --model.")
        hyp = Hypothesis(
            goal=new.goal.strip(),
            hypothesis=(new.hypothesis or "").strip() or None,
            criteria=new.criteria,
            observations=new.observations,
            inputs=new.inputs,
        )
        logger.info("Starting hypothesis %s: %s", hyp.id, hyp.goal)
        task = asyncio.create_task(
            run_hypothesis(hyp, client, build_model, verify_model or build_model)
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        def on_done(t: asyncio.Task[Hypothesis]) -> None:
            if t.cancelled():
                logger.warning("Hypothesis %s was cancelled", hyp.id)
            elif exc := t.exception():
                logger.error("Hypothesis %s failed: %s", hyp.id, exc, exc_info=exc)
            else:
                logger.info("Hypothesis %s completed", hyp.id)

        task.add_done_callback(on_done)
        return hyp

    def _draft_prompt(new: NewDraft) -> str:
        prompt = f"Goal: {new.goal.strip()}"
        if new.hypothesis and new.hypothesis.strip():
            prompt += f"\nProposed hypothesis: {new.hypothesis.strip()}"
        return prompt

    @app.post("/api/criteria")
    async def draft_criteria(new: NewDraft) -> list[Criterion]:
        """Draft a goal's success criteria with the criteria agent, to edit next."""
        if build_model is None:
            raise HTTPException(503, "The server was started without --model.")
        result = await criteria_agent(build_model).run(_draft_prompt(new))
        return result.output

    @app.post("/api/observations")
    async def draft_observations(new: NewDraft) -> list[Observation]:
        """Search the literature for what bears on a goal, for the user to edit.

        The list the user keeps goes back as a new hypothesis's ``observations``,
        and the builder agent is shown it. Searching costs Amass calls, so the page
        asks for this rather than doing it on every run.
        """
        if build_model is None:
            raise HTTPException(503, "The server was started without --model.")
        result = await observations_agent(build_model).run(_draft_prompt(new))
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
        return [n.summary() for n in Registry(registry_dir()).all()]

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
