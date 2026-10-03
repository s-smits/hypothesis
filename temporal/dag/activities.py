import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, TypeAdapter
from temporalio import activity

from node_dag import factory
from node_dag.dag import DagProgress
from node_dag.factory import NodeConfig
from node_dag.nodes.base import BaseScoreConfig
from node_dag.storage import write_atomic
from node_dag.types import Score, Value


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
        inputs: The list of entities for the node's input port.
        values: For a filter, the score of each entity, in order.
    """

    config: NodeConfig
    inputs: dict[str, list[Value]]
    values: list[float] | None = None

    def cache_path(self) -> Path:
        """``$NODE_DAG_RESULTS/nodes/<node name>/<hash>.json``. The root defaults to ``results``.

        The hash covers the config, the inputs and the config's ``version``.
        """
        key = {**self.model_dump(mode="json"), "version": self.config.version}
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        return results_root() / "nodes" / self.config.name / f"{digest}.json"


def _cached[T](inp: RunNodeInput, adapter: TypeAdapter[T], run: Callable[[], T]) -> T:
    path = inp.cache_path()
    if path.exists():
        return adapter.validate_json(path.read_bytes())
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
    return _cached(inp, _KEEP, lambda: _run_aligned(inp, values=inp.values))


class SaveWorkflowInput(BaseModel):
    """Input to save_workflow.

    Args:
        workflow_id: The ID of the DagWorkflow run.
        progress: The run's DAG, the status of each step and every value it produced.
    """

    workflow_id: str
    progress: DagProgress

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
