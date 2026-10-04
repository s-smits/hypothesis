import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError
from temporalio import activity

from node_dag import factory, links
from node_dag.dag import DagProgress
from node_dag.factory import NodeConfig
from node_dag.links import Link
from node_dag.nodes.base import BaseScoreConfig
from node_dag.storage import write_atomic
from node_dag.types import Score, Value

_LINKS: TypeAdapter[list[Link]] = TypeAdapter(list[Link])


def results_root() -> Path:
    """``$NODE_DAG_RESULTS``, or ``results`` if it is not set."""
    # ponytail: a local directory, so only workers on this machine share it. Move
    # to S3 when workers run on more than one machine.
    return Path(os.environ.get("NODE_DAG_RESULTS", "results"))


def results_subdir(name: str) -> Path:
    """``<results root>/<name>``: ``hypotheses``, ``registry``, ``requests`` or ``trajectories``.

    One file each: ``<hypothesis id>.json``, ``<node id>.json``, ``<node name>.json`` and
    ``<hypothesis id>-r<round>-<stage>.json``.
    """
    return results_root() / name


class RunNodeInput(BaseModel):
    """Input to the node activities.

    Args:
        config: The node to run.
        inputs: The list of entities for each of the node's input ports.
        values: For a filter, the score of each entity, in order. A filter reading
            several columns gets a dict instead, keyed by column, each list aligned
            with ``inputs``. A single column stays a bare list, so those filters'
            cache keys are unchanged.
        step: Which step of the run this is, for the links a node reports. It says
            nothing about what the node computes, so it is left out of the cache key:
            the same work in another step, or another run, is still a cache hit.
        reference: For a filter that compares with a reference entity, that entity's
            score. Left out of the cache key when it is None, so other filters' keys
            are unchanged.
    """

    config: NodeConfig
    inputs: dict[str, list[Value]]
    values: list[float] | dict[str, list[float]] | None = None
    step: str = ""
    reference: float | None = None

    def cache_path(self) -> Path:
        """``$NODE_DAG_RESULTS/nodes/<node name>/<hash>.json``. The root defaults to ``results``.

        The hash covers the config, the inputs and the config's ``version``.

        A port with nothing on it is left out of the hash. Only an optional port can
        be empty here, since a step whose required port is empty never runs, and an
        unwired port contributes nothing to the result. Leaving it out is what makes
        giving a node an optional port keep, rather than discard, everything it had
        already cached.
        """
        key = {
            **self.model_dump(
                mode="json",
                exclude={"step"} | ({"reference"} if self.reference is None else set()),
            ),
            "version": self.config.version,
        }
        key["inputs"] = {p: v for p, v in key["inputs"].items() if v}
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        return results_root() / "nodes" / self.config.name / f"{digest}.json"


def links_dir(workflow_id: str) -> Path:
    """``$NODE_DAG_RESULTS/links/<workflow id>``: where a run's steps report links."""
    return results_root() / "links" / workflow_id


def links_path(workflow_id: str, step: str) -> Path:
    """A step's reported links. One file per step, so parallel steps never clash."""
    return links_dir(workflow_id) / f"{step}.json"


def step_links(workflow_id: str) -> dict[str, list[Link]]:
    """Every link the steps of a run have reported, keyed by step."""
    found = {}
    for path in sorted(links_dir(workflow_id).glob("*.json")):
        try:
            found[path.stem] = _LINKS.validate_json(path.read_bytes())
        except ValidationError:
            continue  # A file from an older schema says nothing useful now.
    return found


@contextmanager
def _reporting(step: str) -> Iterator[None]:
    """Save the links the node reports, so the UI can offer them while it runs.

    Each link is written as it is reported, not when the step ends, because a link to
    work in progress is worth having while that work is still going.
    """
    try:
        workflow_id = activity.info().workflow_id
    except RuntimeError:
        workflow_id = None
    if workflow_id is None:
        # Not in an activity: a test or a script, with no run to key on.
        yield
        return
    reported: list[Link] = []

    def sink(link: Link) -> None:
        reported.append(link)
        write_atomic(links_path(workflow_id, step), _LINKS.dump_json(reported))

    with links.collecting(sink):
        yield


def _cached[T](inp: RunNodeInput, adapter: TypeAdapter[T], run: Callable[[], T]) -> T:
    path = inp.cache_path()
    if path.exists():
        return adapter.validate_json(path.read_bytes())
    with _reporting(inp.step):
        out = run()
    write_atomic(path, adapter.dump_json(out))
    return out


_ENTITIES: TypeAdapter[list[Value]] = TypeAdapter(list[Value])
_SCORES = TypeAdapter(list[dict[str, Score]])
_KEEP = TypeAdapter(list[bool])


def _run_aligned(inp: RunNodeInput, **extra: object) -> list:
    """Run the node, which must return one result for each entity, in order."""
    out = factory.build(inp.config).run(**inp.inputs, **extra)
    (items,) = inp.inputs.values()
    if len(out) != len(items):
        raise ValueError(
            f"{inp.config.name} gave {len(out)} results for {len(items)} entities"
        )
    return out


@activity.defn
def run_tool(inp: RunNodeInput) -> list[Value]:
    """Run a tool node on its whole list through the factory, or load its cached result."""
    return _cached(inp, _ENTITIES, lambda: factory.build(inp.config).run(**inp.inputs))


@activity.defn
def run_score(inp: RunNodeInput) -> list[dict[str, Score]]:
    """Run a scoring node on its whole list, or load its cached result.

    Returns one dict of scores per entity, in order.
    """
    config = inp.config
    assert isinstance(config, BaseScoreConfig)

    def run() -> list[dict[str, Score]]:
        out = _run_aligned(inp)
        names = set(config.output)
        if bad := [r for r in out if r.keys() != names]:
            raise ValueError(
                f"{config.name} must score {sorted(names)}, got {sorted(bad[0])}"
            )
        return out

    return _cached(inp, _SCORES, run)


@activity.defn
def run_filter(inp: RunNodeInput) -> list[bool]:
    """Run a filter node on its list and score values, or load its cached result.

    Returns whether to keep each entity, in order.
    """
    extra = {} if inp.reference is None else {"reference": inp.reference}
    return _cached(inp, _KEEP, lambda: _run_aligned(inp, values=inp.values, **extra))


class SavedRun(DagProgress):
    """A finished DagWorkflow run as save_workflow writes it.

    Enough to show the run without Temporal. Files saved before the run fields were
    added have only the DagProgress fields, so those default to None.

    Args:
        status: ``COMPLETED``, ``FAILED`` or ``CANCELED``, as Temporal names them.
        start_time: When the run started.
        close_time: When it finished.
        error: Why it failed, if it did.
    """

    status: str | None = None
    start_time: datetime | None = None
    close_time: datetime | None = None
    error: str | None = None


class SaveWorkflowInput(BaseModel):
    """Input to save_workflow.

    Args:
        workflow_id: The ID of the DagWorkflow run.
        progress: The run's DAG, the status of each step, every value it produced
            and how it ended.
    """

    workflow_id: str
    progress: SavedRun

    def path(self) -> Path:
        """``$NODE_DAG_RESULTS/workflows/<workflow id>.json``."""
        return self.path_for(self.workflow_id)

    @staticmethod
    def path_for(workflow_id: str) -> Path:
        """Where save_workflow writes the run ``workflow_id``."""
        return results_root() / "workflows" / f"{workflow_id}.json"


@activity.defn
async def save_workflow(inp: SaveWorkflowInput) -> None:
    """Write a finished or failed DagWorkflow run to disk."""
    write_atomic(inp.path(), inp.progress.model_dump_json(indent=2).encode())
